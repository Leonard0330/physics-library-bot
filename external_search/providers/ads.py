"""NASA ADS provider (https://ui.adsabs.harvard.edu/help/api/). Search and metadata only:
ADS does not host PDFs itself, so a PDF is offered only when the record has an arXiv
e-print, and then the PDF is taken from arXiv. Requires an API token (ADS_API_TOKEN)."""
import re
import urllib.parse
from typing import Callable, List, Optional

from .. import config
from ..base import BaseProvider, Capability
from ..errors import InvalidQueryError, ProviderResponseError
from ..models import AccessStatus, SearchField, SearchPage, SearchResult
from ..utils.doi import doi_for_query, extract_doi
from ._http import Throttle, clean_text, http_get, parse_json
from .arxiv import ArxivProvider, normalize_arxiv_id

_FIELDS = "bibcode,title,author,abstract,pubdate,year,doi,identifier,pub,property"
_BIBCODE_RE = re.compile(r"^\d{4}[A-Za-z0-9.&+]{14}[A-Za-z.]$")
_RESERVED = {"and", "or", "not", "to"}


def _words(query: str) -> List[str]:
    out = []
    for w in re.sub(r"[^\w\-]", " ", query or "").split():
        w = w.strip("-_")
        if w and w.lower() not in _RESERVED:
            out.append(w)
    return out


def build_query(query: str, field: SearchField) -> str:
    """Build the ADS `q` value from untrusted user text."""
    field = SearchField(field)
    if field == SearchField.DOI:
        doi = doi_for_query(query)
        if not doi:
            raise InvalidQueryError("invalid DOI")
        return f'doi:"{doi}"'
    if field == SearchField.ID:
        arx = normalize_arxiv_id(query)
        if not arx:
            raise InvalidQueryError("invalid arXiv id")
        return f'identifier:"arXiv:{arx}"'
    if field == SearchField.AUTO:
        # abs: does not cover authors, so every word may match either field; a multi-word
        # input is also tried as one author phrase (handles "Albert Einstein" vs "Einstein, A.").
        words = _words(query)
        if not words:
            raise InvalidQueryError("empty query")
        each = " AND ".join(f"(abs:{w} OR author:{w})" for w in words)
        if len(words) == 1:
            return each
        return f'author:"{" ".join(words)}" OR ({each})'
    if field == SearchField.TITLE:
        term = " ".join(re.sub(r'["\\\x00-\x1f]', " ", query or "").split())
        if not term:
            raise InvalidQueryError("empty query")
        return f'title:"{term}"'
    words = _words(query)
    if not words:
        raise InvalidQueryError("empty query")
    joined = " AND ".join(words)
    return f"author:({joined})" if field == SearchField.AUTHOR else f"abs:({joined})"


def _lookup_query(identifier: str) -> str:
    s = (identifier or "").strip()
    if _BIBCODE_RE.match(s):
        return f'bibcode:"{s}"'
    arx = normalize_arxiv_id(s)
    if arx:
        return f'identifier:"arXiv:{arx}"'
    doi = extract_doi(s)
    if doi:
        return f'doi:"{doi}"'
    raise InvalidQueryError("invalid ADS identifier (bibcode, arXiv id or DOI)")


def _arxiv_id(identifiers) -> Optional[str]:
    for i in identifiers if isinstance(identifiers, list) else []:
        if isinstance(i, str) and i.lower().startswith("arxiv:"):
            arx = normalize_arxiv_id(i)
            if arx:
                return arx
    return None


def _first(v) -> Optional[str]:
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str) and x.strip()), None)
    return v if isinstance(v, str) and v.strip() else None


def _parse_doc(d) -> Optional[SearchResult]:
    if not isinstance(d, dict):
        raise ProviderResponseError("ADS document is not an object")
    bib = d.get("bibcode")
    if not isinstance(bib, str) or not _BIBCODE_RE.match(bib):
        raise ProviderResponseError("ADS document missing a valid bibcode")
    title = clean_text(_first(d.get("title")))
    if not title:
        return None
    year = None
    if str(d.get("year") or "").isdigit():
        year = int(d["year"])
    elif re.match(r"^\d{4}", str(d.get("pubdate") or "")):
        year = int(d["pubdate"][:4])
    authors = d.get("author")
    authors = [a for a in (_flip(x) for x in authors if isinstance(x, str)) if a] if isinstance(authors, list) else []
    props = {p.upper() for p in d.get("property", []) if isinstance(p, str)} if isinstance(d.get("property"), list) else set()
    arx = _arxiv_id(d.get("identifier"))
    return SearchResult(
        title=title, source="ads", identifier=bib, authors=authors,
        abstract=clean_text(d.get("abstract")), published=None, publication_year=year,
        journal=clean_text(d.get("pub")), doi=_first(d.get("doi")),
        landing_page_url=f"https://ui.adsabs.harvard.edu/abs/{urllib.parse.quote(bib, safe='')}/abstract",
        pdf_url=f"https://arxiv.org/pdf/{arx}" if arx else None,
        access_status=AccessStatus.OPEN if (arx or "OPENACCESS" in props) else AccessStatus.UNKNOWN)


def _flip(name: str) -> str:
    name = " ".join(name.split())
    if name.count(",") == 1:          # ADS: "Last, First" -> "First Last"
        last, first = (x.strip() for x in name.split(","))
        return f"{first} {last}".strip()
    return name


def parse_response(data: bytes, page: int, page_size: int) -> SearchPage:
    obj = parse_json(data, "ADS")
    resp = obj.get("response")
    if not isinstance(resp, dict) or not isinstance(resp.get("docs"), list) \
            or not isinstance(resp.get("numFound"), int):
        raise ProviderResponseError("unexpected ADS response format")
    results = [r for r in (_parse_doc(d) for d in resp["docs"]) if r]
    start = (page - 1) * page_size
    return SearchPage(results=results, page=page, page_size=page_size, total=resp["numFound"],
                      has_next=start + len(resp["docs"]) < resp["numFound"])


class AdsProvider(BaseProvider):
    name = "ads"
    capabilities = Capability.SEARCH | Capability.METADATA | Capability.OPEN_ACCESS_PDF
    search_fields = BaseProvider.search_fields | {SearchField.DOI, SearchField.ID}
    pdf_hosts = ArxivProvider.pdf_hosts          # the only PDFs offered are arXiv e-prints

    @classmethod
    def configured(cls) -> bool:
        return bool(config.ADS_API_TOKEN)

    def __init__(self, fetch: Optional[Callable[[str], bytes]] = None, token: Optional[str] = None,
                 min_interval: float = config.ADS_MIN_INTERVAL, clock=None, sleep=None):
        self._token = token if token is not None else config.ADS_API_TOKEN
        self._fetch = fetch or self._http
        kw = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v}
        self._throttle = Throttle(min_interval, **kw)

    def _http(self, url: str) -> bytes:
        if not self._token:
            raise ProviderResponseError("ADS API token is not configured")
        return http_get(url, config.REQUEST_TIMEOUT, config.ADS_MAX_RESPONSE_BYTES, "ADS",
                        headers={"Authorization": f"Bearer {self._token}"})

    def _get(self, params: dict) -> bytes:
        self._throttle.wait()
        return self._fetch(config.ADS_API_URL + "?" + urllib.parse.urlencode(params))

    def search(self, query, field=SearchField.KEYWORD, page=1, page_size=config.DEFAULT_PAGE_SIZE):
        if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or page_size < 1:
            raise InvalidQueryError("page and page_size must be positive integers")
        page_size = min(page_size, config.MAX_PAGE_SIZE)
        data = self._get({"q": build_query(query, field), "fl": _FIELDS, "rows": page_size,
                          "start": (page - 1) * page_size, "sort": "score desc"})
        return parse_response(data, page, page_size)

    def get_metadata(self, identifier):
        pg = parse_response(self._get({"q": _lookup_query(identifier), "fl": _FIELDS, "rows": 1}), 1, 1)
        return pg.results[0] if pg.results else None

    def get_open_access_pdf(self, identifier):
        r = self.get_metadata(identifier)
        return r.pdf_url if r else None
