"""arXiv provider using the official Atom API (https://info.arxiv.org/help/api/)."""
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from typing import Callable, Optional

from .. import config
from ..base import BaseProvider, Capability
from ..errors import (InvalidQueryError, ProviderNetworkError, ProviderResponseError,
                      ProviderTimeout)
from ..models import AccessStatus, SearchField, SearchPage, SearchResult

NS = {"a": "http://www.w3.org/2005/Atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/",
      "ax": "http://arxiv.org/schemas/atom"}

_ID_RE = re.compile(r"^(?:[a-z\-]+(?:\.[A-Za-z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?$")
_URL_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?/?$", re.I)


def normalize_arxiv_id(text: str) -> Optional[str]:
    """Accept '2101.00001v2', 'arXiv:2101.00001', abs/pdf URLs; return id or None."""
    s = (text or "").strip()
    m = _URL_ID_RE.search(s)
    if m:
        s = m.group(1)
    if s.lower().startswith("arxiv:"):
        s = s[6:]
    return s if _ID_RE.match(s) else None


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(config.ARXIV_MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as e:
        raise ProviderNetworkError(f"arXiv returned HTTP {e.code}") from e
    except (socket.timeout, TimeoutError) as e:
        raise ProviderTimeout("arXiv request timed out") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ProviderTimeout("arXiv request timed out") from e
        raise ProviderNetworkError(f"arXiv connection failed: {e.reason}") from e
    except OSError as e:
        raise ProviderNetworkError(f"arXiv connection failed: {e}") from e


def build_search_query(query: str, field: SearchField) -> str:
    """Build arXiv `search_query` value from untrusted user text."""
    field = SearchField(field)
    if field in (SearchField.TITLE, SearchField.AUTHOR):
        term = re.sub(r'["\\\x00-\x1f]', " ", query or "")
        term = " ".join(term.split())
        if not term:
            raise InvalidQueryError("empty query")
        prefix = "ti" if field == SearchField.TITLE else "au"
        return f'{prefix}:"{term}"'
    words = re.sub(r"[^\w\-]", " ", query or "").split()
    if not words:
        raise InvalidQueryError("empty query")
    return " AND ".join(f"all:{w}" for w in words)


def _txt(el, path) -> str:
    return " ".join((el.findtext(path, "", NS) or "").split())


def _parse_entry(e) -> SearchResult:
    ident = normalize_arxiv_id(_txt(e, "a:id"))
    title = _txt(e, "a:title")
    if not ident or not title:
        raise ProviderResponseError("arXiv entry missing id or title")
    published = None
    try:
        published = date.fromisoformat(_txt(e, "a:published")[:10])
    except ValueError:
        pass
    pdf = next((l.get("href") for l in e.findall("a:link", NS) if l.get("title") == "pdf"), None)
    pdf = (pdf or f"https://arxiv.org/pdf/{ident}").replace("http://", "https://", 1)
    return SearchResult(
        title=title, source="arxiv", identifier=ident,
        authors=[n for n in (_txt(a, "a:name") for a in e.findall("a:author", NS)) if n],
        abstract=_txt(e, "a:summary") or None,
        published=published, publication_year=published.year if published else None,
        journal=_txt(e, "ax:journal_ref") or None, doi=_txt(e, "ax:doi") or None,
        landing_page_url=f"https://arxiv.org/abs/{ident}", pdf_url=pdf,
        access_status=AccessStatus.OPEN)


def parse_feed(data: bytes, page: int, page_size: int) -> SearchPage:
    # stdlib ElementTree: input is size-capped and comes from arXiv over HTTPS.
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ProviderResponseError("arXiv returned invalid XML") from e
    if root.tag != "{%s}feed" % NS["a"]:
        raise ProviderResponseError("unexpected arXiv response format")
    try:
        total = int(root.findtext("os:totalResults", "", NS))
    except ValueError:
        raise ProviderResponseError("arXiv response lacks totalResults") from None
    results = []
    for e in root.findall("a:entry", NS):
        if "/api/errors" in _txt(e, "a:id"):
            raise ProviderResponseError("arXiv error: " + (_txt(e, "a:summary") or "unknown"))
        results.append(_parse_entry(e))
    start = (page - 1) * page_size
    return SearchPage(results=results, page=page, page_size=page_size, total=total,
                      has_next=start + len(results) < total)


class ArxivProvider(BaseProvider):
    name = "arxiv"
    capabilities = Capability.SEARCH | Capability.METADATA | Capability.OPEN_ACCESS_PDF
    pdf_hosts = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})

    def __init__(self, fetch: Optional[Callable[[str], bytes]] = None,
                 min_interval: float = config.ARXIV_MIN_INTERVAL,
                 clock=time.monotonic, sleep=time.sleep):
        self._fetch = fetch or (lambda url: _http_get(url, config.REQUEST_TIMEOUT))
        self._min_interval, self._clock, self._sleep = min_interval, clock, sleep
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def _throttle(self):
        with self._lock:  # serialize calls to honour arXiv's 1 request / 3 s policy
            if self._last is not None:
                wait = self._last + self._min_interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()

    def _get(self, params: dict) -> bytes:
        self._throttle()
        return self._fetch(config.ARXIV_API_URL + "?" + urllib.parse.urlencode(params))

    def search(self, query, field=SearchField.KEYWORD, page=1, page_size=config.DEFAULT_PAGE_SIZE):
        if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or page_size < 1:
            raise InvalidQueryError("page and page_size must be positive integers")
        page_size = min(page_size, config.MAX_PAGE_SIZE)
        sq = build_search_query(query, field)
        data = self._get({"search_query": sq, "start": (page - 1) * page_size,
                          "max_results": page_size, "sortBy": "relevance",
                          "sortOrder": "descending"})
        return parse_feed(data, page, page_size)

    def get_metadata(self, identifier):
        ident = normalize_arxiv_id(identifier)
        if not ident:
            raise InvalidQueryError("invalid arXiv identifier")
        pg = parse_feed(self._get({"id_list": ident, "max_results": 1}), 1, 1)
        return pg.results[0] if pg.results else None

    def get_open_access_pdf(self, identifier):
        ident = normalize_arxiv_id(identifier)
        if not ident:
            raise InvalidQueryError("invalid arXiv identifier")
        return f"https://arxiv.org/pdf/{ident}"
