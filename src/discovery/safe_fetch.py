"""Bounded public-page reader, intentionally separate from saving a bookmark.

Callers must establish source-use permission and pass any metered call through
the provider gateway before using this reader. No script, cookie, or subresource
is executed. DNS is checked once, then the socket is pinned to that public IP;
TLS still verifies the original hostname. Every redirect repeats these checks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
import http.client
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit, urljoin


class FetchRejected(ValueError):
    def __init__(self, code, *, http_status=None, retry_after_seconds=None):
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)  # Never copy a private URL/response into errors.


@dataclass(frozen=True)
class Page:
    url: str
    mime: str
    content: bytes
    redirects: int


_DNS_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='public-page-dns')
_DNS_SLOTS = threading.BoundedSemaphore(2)
MIMES = frozenset({'text/html', 'text/plain', 'application/json', 'application/xhtml+xml'})


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise FetchRejected('INVALID_IP') from None
    if not address.is_global or address.is_multicast or address.is_unspecified or getattr(address, 'ipv4_mapped', None):
        raise FetchRejected('NON_PUBLIC_IP')
    return str(address)


def validate_public_url(value):
    """Syntactic checks only; a valid link is not permission to fetch it."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise FetchRejected('INVALID_URL')
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise FetchRejected('UNSUPPORTED_URL')
        host = parsed.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if not host or '\\' in value or '%' in host or host in {'localhost', 'metadata.google.internal'} or host.endswith(('.localhost', '.local', '.internal')):
            raise FetchRejected('NON_PUBLIC_HOST')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port != (443 if parsed.scheme == 'https' else 80):
            raise FetchRejected('UNSUPPORTED_PORT')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            public_ip(host)
        authority = '[' + host + ']' if ':' in host else host
        return urlunsplit((parsed.scheme, authority, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, FetchRejected):
            raise
        raise FetchRejected('INVALID_URL') from None


def _resolve(host, port, deadline):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchRejected('DNS_BUSY')
    def lookup():
        try:
            return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        finally:
            _DNS_SLOTS.release()
    future = _DNS_POOL.submit(lookup)
    try:
        records = future.result(timeout=max(.001, deadline - time.monotonic()))
    except FutureTimeout:
        raise FetchRejected('FETCH_TIMEOUT') from None
    except OSError:
        raise FetchRejected('DNS_FAILED') from None
    # Preserve DNS round-robin ordering instead of always pinning the smallest IP.
    ips = list(dict.fromkeys(public_ip(record[4][0]) for record in records))
    if not ips:
        raise FetchRejected('DNS_FAILED')
    return ips


def _connect(parsed, ip, timeout):
    """No second hostname lookup, even if DNS changes after validation."""
    port = 443 if parsed.scheme == 'https' else 80
    host = parsed.hostname
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    family = socket.AF_INET6 if ':' in ip else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((ip, port))
        peer = public_ip(sock.getpeername()[0])
        if ipaddress.ip_address(peer) != ipaddress.ip_address(ip):
            raise FetchRejected('PEER_MISMATCH')
        if parsed.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        connection.sock = sock
        return connection
    except BaseException:
        sock.close()
        raise


def fetch_public(url, *, max_bytes=1_048_576, timeout_seconds=8, max_redirects=3,
                 resolver=None, connector=None):
    """Test-injectable transport; returned bytes must never be rendered as HTML."""
    if not 1 <= max_bytes <= 2_097_152 or not .05 <= timeout_seconds <= 15 or not 0 <= max_redirects <= 5:
        raise FetchRejected('INVALID_LIMIT')
    resolver, connector = resolver or _resolve, connector or _connect
    deadline = time.monotonic() + timeout_seconds
    current, seen = validate_public_url(url), set()
    for redirect in range(max_redirects + 1):
        if current in seen:
            raise FetchRejected('REDIRECT_LOOP')
        seen.add(current)
        parsed = urlsplit(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchRejected('FETCH_TIMEOUT')
        addresses = resolver(parsed.hostname, 443 if parsed.scheme == 'https' else 80, deadline)
        addresses = [public_ip(address) for address in addresses]
        if not addresses:
            raise FetchRejected('DNS_FAILED')
        connection = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchRejected('FETCH_TIMEOUT')
            # Connectivity fallback is only before any HTTP request is sent. Never
            # retry an HTTP refusal (429/403/504) through another IP or mirror.
            last_error = None
            selected_ip = None
            for ip in addresses[:2]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchRejected('FETCH_TIMEOUT')
                try:
                    connection = connector(parsed, ip, min(5, remaining))
                    selected_ip = ip
                    break
                except ssl.SSLCertVerificationError:
                    raise FetchRejected('TLS_CERTIFICATE_ERROR') from None
                except ssl.SSLError:
                    raise FetchRejected('TLS_FAILED') from None
                except OSError as exc:
                    last_error = exc
            if connection is None:
                raise FetchRejected('FETCH_TIMEOUT' if isinstance(last_error, TimeoutError) else 'CONNECT_FAILED')
            if connection.sock is not None:
                peer = public_ip(connection.sock.getpeername()[0])
                if ipaddress.ip_address(peer) != ipaddress.ip_address(selected_ip):
                    raise FetchRejected('PEER_MISMATCH')
                connection.sock.settimeout(max(.001, deadline - time.monotonic()))
            connection.request('GET', urlunsplit(('', '', parsed.path, parsed.query, '')),
                headers={'Accept': ', '.join(sorted(MIMES)), 'Accept-Encoding': 'identity',
                         'User-Agent': 'TravelAgent-SourceCheck/1', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location or redirect == max_redirects:
                    raise FetchRejected('REDIRECT_LIMIT')
                current = validate_public_url(urljoin(current, location))
                continue
            if response.status != 200:
                raise FetchRejected('HTTP_UNAVAILABLE', http_status=response.status,
                                    retry_after_seconds=retry_after(response.getheader('Retry-After')))
            mime = (response.getheader('Content-Type') or '').split(';', 1)[0].strip().lower()
            if mime not in MIMES or response.getheader('Content-Encoding', 'identity') not in {'identity', ''}:
                raise FetchRejected('UNSUPPORTED_MIME')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                raise FetchRejected('RESPONSE_TOO_LARGE')
            chunks, received = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchRejected('FETCH_TIMEOUT')
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(65536, max_bytes + 1 - received))
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise FetchRejected('RESPONSE_TOO_LARGE')
                chunks.append(chunk)
            return Page(current, mime, b''.join(chunks), redirect)
        except FetchRejected:
            raise
        except TimeoutError:
            raise FetchRejected('FETCH_TIMEOUT') from None
        except ssl.SSLCertVerificationError:
            raise FetchRejected('TLS_CERTIFICATE_ERROR') from None
        except ssl.SSLError:
            raise FetchRejected('TLS_FAILED') from None
        except (ConnectionError, http.client.HTTPException):
            raise FetchRejected('RESPONSE_INTERRUPTED') from None
        except (OSError, ValueError):
            raise FetchRejected('FETCH_FAILED') from None
        finally:
            if connection is not None:
                connection.close()
    raise FetchRejected('REDIRECT_LIMIT')


def retry_after(value):
    """Retain only a bounded numeric delay; no response body or headers in logs."""
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        seconds = int(value) if value.strip().isdigit() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return max(0, min(604800, int(seconds)))
    except (ValueError, TypeError, OverflowError):
        return None
