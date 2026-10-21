"""Tiny HTTP client for the local Going app API (standard library only).

- Talks only to a loopback base URL (local-only MCP bridge, PRD decision 3 = A).
  Proxies are switched off (``ProxyHandler({})``): with HTTP(S)_PROXY set, the
  default urllib opener would hand the Bearer token to the proxy host instead.
- Sends the personal access token as ``Authorization: Bearer``; never logs it.
- Only GET and POST ``/ask`` are issued by the tool layer; the app enforces the
  same allowlist server-side, so this is a second fence, not the only one.
- Errors come back as ``GoingAPIError`` (status, code, message), never as a raw
  traceback that could echo headers.
"""
from __future__ import annotations

import json
from urllib import error, parse, request

LOOPBACK_HOSTS = {'localhost', '127.0.0.1', '::1'}
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 30.0
# POST /ask can run the paid trip agent: a step may start just before its 25 s
# deadline and the provider call itself may take up to 45 s (openai_adapter),
# plus the router, tools and grounding. 120 s covers that chat path so a slow
# answer is not reported as "app not running" while it is still billed.
# search_mail's embedding now inherits the agent deadline (no new send or retry
# after 25 s), so the worst case is one call started just before it. 120 s is
# still not enforced by the server — hence ASK_TIMEOUT, not NETWORK_ERROR.
ASK_TIMEOUT = 120.0
API_PREFIX = '/api/v2'


class GoingAPIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f'{status} {code}: {message}')
        self.status, self.code, self.message = status, code, message


def validate_base_url(base_url):
    parts = parse.urlsplit(base_url or '')
    if parts.scheme not in {'http', 'https'} or parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError('GOING_BASE_URL must be a loopback URL such as http://127.0.0.1:8000 (local only).')
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {'', '/'}:
        raise ValueError('GOING_BASE_URL must be an origin only (no path, query or credentials).')
    return f'{parts.scheme}://{parts.netloc}'


def segment(value):
    """Encode one path segment; rejects empty values and traversal."""
    text = str(value or '')
    if not text or len(text) > 200 or text in {'.', '..'}:
        raise GoingAPIError(0, 'INVALID_ARGUMENT', 'ID 값이 올바르지 않습니다.')
    return parse.quote(text, safe='')


class GoingClient:
    def __init__(self, base_url, token, *, timeout=DEFAULT_TIMEOUT, ask_timeout=ASK_TIMEOUT, opener=None):
        self.base_url = validate_base_url(base_url)
        if not token or any(ch.isspace() for ch in token):
            raise ValueError('A personal access token is required (GOING_TOKEN or GOING_TOKEN_FILE).')
        self._token = token
        self.timeout = timeout
        self.ask_timeout = max(timeout, ask_timeout)
        self._open = opener or request.build_opener(request.ProxyHandler({}), _NoRedirect()).open

    def __repr__(self):
        return f'GoingClient(base_url={self.base_url!r}, token=<hidden>)'

    def _send(self, method, path, *, params=None, body=None, timeout=None):
        if method not in {'GET', 'POST'}:
            raise GoingAPIError(0, 'METHOD_NOT_ALLOWED', '읽기 조회와 질문만 보낼 수 있습니다.')
        query = parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base_url + API_PREFIX + path + ('?' + query if query else '')
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=timeout or self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1) if exc.fp else b''
            status = exc.code
            try:
                payload = json.loads(raw.decode() or '{}')
            except (ValueError, UnicodeDecodeError):
                payload = {}
            detail = payload.get('error') if isinstance(payload, dict) else None
            detail = detail if isinstance(detail, dict) else {}
            raise GoingAPIError(status, str(detail.get('code') or 'HTTP_ERROR'),
                                str(detail.get('message') or f'요청이 실패했습니다 ({status}).')) from None
        except TimeoutError:
            # urllib wraps connect/send failures in URLError; a bare timeout means the
            # request reached the app and the response did not arrive in time.
            if method == 'POST':
                raise GoingAPIError(0, 'ASK_TIMEOUT', '응답을 기다리다 시간이 초과됐습니다. 앱에서 답변이 계속 처리됐을 수 있고 '
                                    '비용이 들었을 수 있습니다. 같은 질문을 바로 다시 보내지 말고 앱에서 먼저 확인해 주세요.') from None
            raise GoingAPIError(0, 'TIMEOUT', '로컬 고잉 앱의 응답이 늦어 중단했습니다. 잠시 뒤 다시 시도해 주세요.') from None
        except (error.URLError, OSError) as exc:
            raise GoingAPIError(0, 'NETWORK_ERROR', '로컬 고잉 앱에 연결하지 못했습니다. 앱이 실행 중인지 확인해 주세요.') from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GoingAPIError(status, 'RESPONSE_TOO_LARGE', '응답이 너무 큽니다.')
        try:
            return json.loads(raw.decode() or '{}')
        except (ValueError, UnicodeDecodeError):
            raise GoingAPIError(status, 'INVALID_RESPONSE', '응답을 해석하지 못했습니다.') from None

    def get(self, path, params=None):
        return self._send('GET', path, params=params)

    def ask(self, trip_id, question):
        return self._send('POST', f'/trips/{segment(trip_id)}/ask', body={'question': question, 'history': []},
                          timeout=self.ask_timeout)


class _NoRedirect(request.HTTPRedirectHandler):
    """Never follow redirects (the Authorization header must not travel)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
