"""The one HTTP client the sources and the AI share, and the shape every fake in the tests follows:

    request(method, url, body=None, headers=None, timeout=30) -> Response(status, url, body)

Redirects are followed for every method (CalDAV's PROPFIND and REPORT get moved between iCloud's
hosts; urllib only follows GET). A status of 400 or more raises HTTPError, so callers see failures
in one place. Standard library only.
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MAX_REDIRECTS = 5
USER_AGENT = "slowapp-calendar"


@dataclass
class Response:
    status: int
    url: str
    body: bytes

    def json(self):
        return json.loads(self.body)

    def text(self):
        return self.body.decode("utf-8", errors="replace")


class HTTPError(Exception):
    """An HTTP status of 400 or more (status set), or no answer at all (status None)."""

    def __init__(self, status, url, detail=""):
        host = urllib.parse.urlsplit(url).netloc
        super().__init__(f"{status or 'no answer'} from {host}" + (f": {detail[:300]}" if detail else ""))
        self.status, self.url, self.detail = status, url, detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                       # surfaces as an HTTPError we follow ourselves


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_request(method, url, body=None, headers=None, timeout=30):
    """The real client. Never logs or echoes headers (they carry tokens and passwords)."""
    hdrs = dict({"User-Agent": USER_AGENT}, **(headers or {}))
    for _ in range(MAX_REDIRECTS + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with _opener.open(req, timeout=timeout) as r:
                return Response(r.status, r.geturl(), r.read())
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                if e.code == 303:
                    method, body = "GET", None
                continue
            raise HTTPError(e.code, url, e.read().decode(errors="replace")) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise HTTPError(None, url, str(getattr(e, "reason", e))) from None
    raise HTTPError(None, url, "too many redirects")


def get_json(request, url, headers=None, timeout=30):
    r = request("GET", url, None, headers, timeout)
    try:
        return r.json()
    except ValueError as e:
        raise HTTPError(r.status, url, f"not JSON ({e})") from None
