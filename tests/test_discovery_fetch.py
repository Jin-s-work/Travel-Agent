"""No network: hostile URLs, DNS rebinding, redirects, byte and time caps."""
import io
from types import SimpleNamespace
import pytest
from src.discovery.safe_fetch import FetchRejected, fetch_public, validate_public_url


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'gopher://example.org', 'http://127.0.0.1/',
    'http://[::1]/', 'http://169.254.169.254/latest/meta-data', 'http://10.0.0.1/',
    'https://user:password@example.org/', 'http://localhost/', 'http://server.internal/',
    'http://[::ffff:127.0.0.1]/', 'https://example.org:8443/', 'https://example.org/\r\nx'])
def test_unsafe_link_rejected(url):
    with pytest.raises(FetchRejected):
        validate_public_url(url)


class Response:
    def __init__(self, content=b'<script>neverExecute()</script>', headers=None, status=200):
        self.status, self.content = status, io.BytesIO(content)
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
    def getheader(self, name, default=None):
        return self.headers.get(name, default)
    def read(self, amount):
        return self.content.read(amount)


class Connection:
    def __init__(self, response, peer='93.184.216.34'):
        self.response, self.requests, self.closed = response, [], False
        self.sock = SimpleNamespace(getpeername=lambda: (peer, 443), settimeout=lambda _: None)
    def request(self, *args, **kwargs): self.requests.append((args, kwargs))
    def getresponse(self): return self.response
    def close(self): self.closed = True


def invoke(response, **kwargs):
    con = Connection(response)
    return fetch_public('https://example.org/page', resolver=lambda *_: ['93.184.216.34'],
        connector=lambda *_: con, **kwargs), con


def test_reader_returns_inert_bytes_and_sends_no_credentials():
    page, con = invoke(Response())
    assert page.content == b'<script>neverExecute()</script>' and page.mime == 'text/html'
    assert con.closed
    assert set(con.requests[0][1]['headers']) == {'Accept', 'Accept-Encoding', 'User-Agent', 'Connection'}


def test_any_private_dns_answer_blocks_entire_connection():
    calls = []
    with pytest.raises(FetchRejected, match='NON_PUBLIC_IP'):
        fetch_public('https://example.org/', resolver=lambda *_: ['93.184.216.34', '10.0.0.1'],
                     connector=lambda *args: calls.append(args))
    assert calls == []


def test_connected_peer_cannot_change_to_private_or_other_public_ip():
    for peer, code in [('127.0.0.1', 'NON_PUBLIC_IP'), ('8.8.8.8', 'PEER_MISMATCH')]:
        con = Connection(Response(), peer)
        with pytest.raises(FetchRejected, match=code):
            fetch_public('https://example.org/', resolver=lambda *_: ['93.184.216.34'], connector=lambda *_: con)
        assert con.requests == [] and con.closed


def test_redirect_revalidates_dns_and_never_sends_to_private_target():
    targets = []
    con = Connection(Response(headers={'Location': 'https://redirect.example.org/'}, status=302))
    def resolve(host, *_):
        targets.append(host)
        return ['93.184.216.34'] if host == 'example.org' else ['192.168.1.2']
    with pytest.raises(FetchRejected, match='NON_PUBLIC_IP'):
        fetch_public('https://example.org/', resolver=resolve, connector=lambda *_: con)
    assert targets == ['example.org', 'redirect.example.org'] and len(con.requests) == 1


@pytest.mark.parametrize('response,code', [
    (Response(b'x'*11), 'RESPONSE_TOO_LARGE'),
    (Response(headers={'Content-Type': 'text/html', 'Content-Length': '10000'}), 'RESPONSE_TOO_LARGE'),
    (Response(headers={'Content-Type': 'image/png'}), 'UNSUPPORTED_MIME'),
    (Response(headers={'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}), 'UNSUPPORTED_MIME'),
    (Response(status=403), 'HTTP_UNAVAILABLE'),
    (Response(status=302, headers={'Location': 'file:///etc/passwd'}), 'UNSUPPORTED_URL'),
])
def test_response_limits_and_formats(response, code):
    with pytest.raises(FetchRejected, match=code):
        invoke(response, max_bytes=10)


def test_redirect_loop_and_deadline(monkeypatch):
    with pytest.raises(FetchRejected, match='REDIRECT_LOOP'):
        invoke(Response(status=302, headers={'Location': '/page'}))
    clock = iter([0, 0, 0, 20])
    monkeypatch.setattr('src.discovery.safe_fetch.time.monotonic', lambda: next(clock))
    with pytest.raises(FetchRejected, match='FETCH_TIMEOUT'):
        invoke(Response())


def test_real_connector_pins_ip_and_keeps_tls_hostname(monkeypatch):
    from urllib.parse import urlsplit
    from src.discovery.safe_fetch import _connect
    actions = []
    sock = SimpleNamespace(settimeout=lambda t: None, connect=lambda addr: actions.append(('connect', addr)),
        getpeername=lambda: ('93.184.216.34',443), close=lambda: None)
    monkeypatch.setattr('src.discovery.safe_fetch.socket.socket', lambda *a: sock)
    monkeypatch.setattr('src.discovery.safe_fetch.ssl.create_default_context', lambda: SimpleNamespace(
        wrap_socket=lambda s, server_hostname: actions.append(('tls', server_hostname)) or s))
    connection = _connect(urlsplit('https://example.org/'), '93.184.216.34', 2)
    assert actions == [('connect', ('93.184.216.34', 443)), ('tls', 'example.org')]
    assert connection.sock is sock
