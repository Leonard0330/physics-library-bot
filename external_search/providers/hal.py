"""HAL provider (https://api.archives-ouvertes.fr/docs/search). Open-access documents whose
file is hosted by HAL itself; PDFs are only taken from HAL's own domains."""
import re
import urllib.parse
from datetime import date
from typing import Callable, List, Optional

from .. import config
from ..base import BaseProvider, Capability
from ..errors import InvalidQueryError, ProviderResponseError
from ..models import AccessStatus, SearchField, SearchPage, SearchResult
from ..utils.doi import doi_for_query
from ..utils.pdf import host_allowed
from ._http import Throttle, http_get, parse_json

PDF_HOSTS = frozenset({"hal.science", ".hal.science", "hal.archives-ouvertes.fr",
                       ".archives-ouvertes.fr"})
_FIELDS = ("halId_s,title_s,authFullName_s,abstract_s,producedDate_s,publicationDateY_i,"
           "doiId_s,uri_s,fileMain_s,openAccess_bool,journalTitle_s")
_ID_RE = re.compile(r"^[A-Za-z0-9]+-\d{6,10}(?:v\d+)?$")
_URL_ID_RE = re.compile(r"(?:hal\.science|archives-ouvertes\.fr)/([A-Za-z0-9]+-\d{6,10}(?:v\d+)?)", re.I)
_RESERVED = {"and", "or", "not", "to"}


def normalize_hal_id(text: str) -> Optional[str]:
    s = (text or "").strip()
    m = _URL_ID_RE.search(s)
    if m:
        return m.group(1)
    return s if _ID_RE.match(s) else None


def _words(query: str) -> List[str]:
    out = []
    for w in re.sub(r"[^\w\-]", " ", query or "").split():
        w = w.strip("-_")
        if w and w.lower() not in _RESERVED:
            out.append(w)
    return out


def build_query(query: str, field: SearchField) -> str:
    """Build the Solr `q` value from untrusted user text."""
    field = SearchField(field)
    if field == SearchField.DOI:      # doiId_s is a case-sensitive string field
        doi = doi_for_query(query)
        if not doi:
            raise InvalidQueryError("invalid DOI")
        variants = dict.fromkeys((doi, doi.upper()))
        return "doiId_s:(" + " OR ".join(f'"{v}"' for v in variants) + ")"
    if field == SearchField.TITLE:
        term = " ".join(re.sub(r'["\\\x00-\x1f]', " ", query or "").split())
        if not term:
            raise InvalidQueryError("empty query")
        return f'title_t:"{term}"'
    words = _words(query)
    if not words:
        raise InvalidQueryError("empty query")
    joined = " AND ".join(words)
    if field == SearchField.AUTHOR:
        return f"authFullName_t:({joined})"
    if field == SearchField.AUTO:
        return f"({joined}) OR authFullName_t:({joined})"
    return joined


def _first(v) -> Optional[str]:
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str) and x.strip()), None)
    return " ".join(v.split()) if isinstance(v, str) and v.strip() else None


def _https_on_hal(url) -> Optional[str]:
    if not isinstance(url, str):
        return None
    url = url.replace("http://", "https://", 1)
    parts = urllib.parse.urlsplit(url)
    ok = parts.scheme == "https" and host_allowed(parts.hostname or "", PDF_HOSTS)
    return url if ok else None


def _parse_doc(d) -> Optional[SearchResult]:
    if not isinstance(d, dict):
        raise ProviderResponseError("HAL document is not an object")
    hal_id = d.get("halId_s")
    if not isinstance(hal_id, str) or not _ID_RE.match(hal_id):
        raise ProviderResponseError("HAL document missing a valid halId_s")
    title = _first(d.get("title_s"))
    if not title:
        return None
    published, year = None, d.get("publicationDateY_i") if isinstance(d.get("publicationDateY_i"), int) else None
    pd = str(d.get("producedDate_s") or "")
    try:
        published = date.fromisoformat(pd[:10])
        year = published.year
    except ValueError:
        pass
    authors = d.get("authFullName_s")
    authors = [a.strip() for a in authors if isinstance(a, str) and a.strip()] if isinstance(authors, list) else []
    oa = d.get("openAccess_bool")
    pdf = _https_on_hal(d.get("fileMain_s")) if oa is True else None
    return SearchResult(
        title=title, source="hal", identifier=hal_id, authors=authors,
        abstract=_first(d.get("abstract_s")), published=published, publication_year=year,
        journal=_first(d.get("journalTitle_s")), doi=_first(d.get("doiId_s")),
        landing_page_url=_https_on_hal(d.get("uri_s")) or f"https://hal.science/{hal_id}",
        pdf_url=pdf,
        access_status=AccessStatus.OPEN if (oa is True and pdf) else
        AccessStatus.CLOSED if oa is False else AccessStatus.UNKNOWN)


def parse_response(data: bytes, page: int, page_size: int) -> SearchPage:
    obj = parse_json(data, "HAL")
    resp = obj.get("response")
    if not isinstance(resp, dict) or not isinstance(resp.get("docs"), list) \
            or not isinstance(resp.get("numFound"), int):
        raise ProviderResponseError("unexpected HAL response format")
    results = [r for r in (_parse_doc(d) for d in resp["docs"]) if r]
    start = (page - 1) * page_size
    return SearchPage(results=results, page=page, page_size=page_size, total=resp["numFound"],
                      has_next=start + len(resp["docs"]) < resp["numFound"])


class HalProvider(BaseProvider):
    name = "hal"
    capabilities = Capability.SEARCH | Capability.METADATA | Capability.OPEN_ACCESS_PDF
    pdf_hosts = PDF_HOSTS
    search_fields = BaseProvider.search_fields | {SearchField.DOI}

    def __init__(self, fetch: Optional[Callable[[str], bytes]] = None,
                 min_interval: float = config.HAL_MIN_INTERVAL, clock=None, sleep=None):
        self._fetch = fetch or (lambda url: http_get(url, config.REQUEST_TIMEOUT,
                                                     config.HAL_MAX_RESPONSE_BYTES, "HAL"))
        kw = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v}
        self._throttle = Throttle(min_interval, **kw)

    def _get(self, params: dict) -> bytes:
        self._throttle.wait()
        return self._fetch(config.HAL_API_URL + "?" + urllib.parse.urlencode(params, doseq=True))

    def search(self, query, field=SearchField.KEYWORD, page=1, page_size=config.DEFAULT_PAGE_SIZE):
        if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or page_size < 1:
            raise InvalidQueryError("page and page_size must be positive integers")
        page_size = min(page_size, config.MAX_PAGE_SIZE)
        data = self._get({"q": build_query(query, field),
                          "fq": ["openAccess_bool:true", "fileMain_s:[* TO *]"],
                          "fl": _FIELDS, "rows": page_size, "start": (page - 1) * page_size,
                          "wt": "json"})
        return parse_response(data, page, page_size)

    def get_metadata(self, identifier):
        hal_id = normalize_hal_id(identifier)
        if not hal_id:
            raise InvalidQueryError("invalid HAL identifier")
        pg = parse_response(self._get({"q": f"halId_s:{hal_id}", "fl": _FIELDS, "rows": 1, "wt": "json"}), 1, 1)
        return pg.results[0] if pg.results else None

    def get_open_access_pdf(self, identifier):
        r = self.get_metadata(identifier)
        return r.pdf_url if r else None
