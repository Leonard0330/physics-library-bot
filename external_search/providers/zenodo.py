"""Zenodo provider (https://developers.zenodo.org/). Open-access publications whose PDF
is hosted by Zenodo itself; PDFs are only taken from zenodo.org."""
import re
import urllib.parse
from datetime import date
from typing import Callable, List, Optional

from .. import config
from ..base import BaseProvider, Capability
from ..errors import InvalidQueryError, ProviderResponseError
from ..models import AccessStatus, SearchField, SearchPage, SearchResult
from ._http import NotFoundError, Throttle, clean_text, http_get, parse_json

_RESERVED = {"and", "or", "not", "to"}
_ID_RE = re.compile(r"^(?:zenodo\.|10\.5281/zenodo\.)?(\d{1,12})$", re.I)
_URL_ID_RE = re.compile(r"zenodo\.org/(?:records?|api/records)/(\d{1,12})", re.I)


def normalize_zenodo_id(text: str) -> Optional[str]:
    """Accept '1234567', 'zenodo.1234567', '10.5281/zenodo.1234567', record URLs."""
    s = (text or "").strip()
    m = _URL_ID_RE.search(s)
    if m:
        return m.group(1)
    m = _ID_RE.match(s)
    return m.group(1) if m else None


def _words(query: str) -> List[str]:
    out = []
    for w in re.sub(r"[^\w\-]", " ", query or "").split():
        w = w.strip("-_")
        if w and w.lower() not in _RESERVED:
            out.append(w)
    return out


def build_query(query: str, field: SearchField) -> str:
    """Build the `q` value (Elasticsearch query string) from untrusted user text."""
    field = SearchField(field)
    if field == SearchField.TITLE:
        term = " ".join(re.sub(r'["\\\x00-\x1f]', " ", query or "").split())
        if not term:
            raise InvalidQueryError("empty query")
        return f'title:"{term}"'
    words = _words(query)
    if not words:
        raise InvalidQueryError("empty query")
    joined = " AND ".join(words)
    return f"creators.name:({joined})" if field == SearchField.AUTHOR else joined


def _author(name: str) -> str:
    name = " ".join((name or "").split())
    if name.count(",") == 1:          # "Last, First" -> "First Last"
        last, first = (x.strip() for x in name.split(","))
        return f"{first} {last}".strip()
    return name


def _pdf_key(rec: dict) -> Optional[str]:
    for f in rec.get("files") or []:
        if not isinstance(f, dict):
            continue
        key = f.get("key") or f.get("filename")
        if not (isinstance(key, str) and key.lower().endswith(".pdf")):
            continue
        size = f.get("size") if f.get("size") is not None else f.get("filesize")
        if isinstance(size, int) and size > config.PDF_MAX_BYTES:
            continue                   # would be rejected by the download limit anyway
        return key
    return None


def _parse_record(rec) -> Optional[SearchResult]:
    if not isinstance(rec, dict):
        raise ProviderResponseError("Zenodo record is not an object")
    rid = rec.get("id")
    meta = rec.get("metadata")
    if not isinstance(rid, int) or not isinstance(meta, dict):
        raise ProviderResponseError("Zenodo record missing id or metadata")
    title = " ".join(str(meta.get("title") or "").split())
    if not title:
        return None
    published, year = None, None
    pd = str(meta.get("publication_date") or "")
    try:
        published = date.fromisoformat(pd[:10])
        year = published.year
    except ValueError:
        if re.match(r"^\d{4}", pd):
            year = int(pd[:4])
    right = str(meta.get("access_right") or "").lower()
    access = (AccessStatus.OPEN if right == "open" else
              AccessStatus.CLOSED if right in ("closed", "restricted", "embargoed") else
              AccessStatus.UNKNOWN)
    key = _pdf_key(rec) if access == AccessStatus.OPEN else None
    pdf = (f"https://zenodo.org/api/records/{rid}/files/{urllib.parse.quote(key, safe='')}/content"
           if key else None)
    creators = meta.get("creators") if isinstance(meta.get("creators"), list) else []
    return SearchResult(
        title=title, source="zenodo", identifier=f"zenodo.{rid}",
        authors=[a for a in (_author(c.get("name", "")) for c in creators if isinstance(c, dict)) if a],
        abstract=clean_text(meta.get("description")),
        published=published, publication_year=year,
        journal=((meta.get("journal") or {}).get("title") if isinstance(meta.get("journal"), dict) else None),
        doi=(rec.get("doi") or meta.get("doi") or None),
        landing_page_url=f"https://zenodo.org/records/{rid}",
        pdf_url=pdf, access_status=access)


def parse_hits(data: bytes, page: int, page_size: int) -> SearchPage:
    obj = parse_json(data, "Zenodo")
    hits = obj.get("hits")
    if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
        raise ProviderResponseError("unexpected Zenodo response format")
    total = hits.get("total")
    if isinstance(total, dict):
        total = total.get("value")
    if not isinstance(total, int):
        raise ProviderResponseError("Zenodo response lacks total")
    results = [r for r in (_parse_record(h) for h in hits["hits"]) if r]
    start = (page - 1) * page_size
    return SearchPage(results=results, page=page, page_size=page_size, total=total,
                      has_next=start + len(hits["hits"]) < total)


class ZenodoProvider(BaseProvider):
    name = "zenodo"
    capabilities = Capability.SEARCH | Capability.METADATA | Capability.OPEN_ACCESS_PDF
    pdf_hosts = frozenset({"zenodo.org"})

    def __init__(self, fetch: Optional[Callable[[str], bytes]] = None,
                 min_interval: float = config.ZENODO_MIN_INTERVAL, clock=None, sleep=None):
        self._fetch = fetch or (lambda url: http_get(url, config.REQUEST_TIMEOUT,
                                                     config.ZENODO_MAX_RESPONSE_BYTES, "Zenodo"))
        kw = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v}
        self._throttle = Throttle(min_interval, **kw)

    def _get(self, url: str) -> bytes:
        self._throttle.wait()
        return self._fetch(url)

    def search(self, query, field=SearchField.KEYWORD, page=1, page_size=config.DEFAULT_PAGE_SIZE):
        if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or page_size < 1:
            raise InvalidQueryError("page and page_size must be positive integers")
        page_size = min(page_size, config.MAX_PAGE_SIZE, 25)       # anonymous maximum is 25
        params = {"q": build_query(query, field), "size": page_size, "page": page,
                  "sort": "bestmatch", "type": "publication", "file_type": "pdf"}
        return parse_hits(self._get(config.ZENODO_API_URL + "?" + urllib.parse.urlencode(params)),
                          page, page_size)

    def get_metadata(self, identifier):
        rid = normalize_zenodo_id(identifier)
        if not rid:
            raise InvalidQueryError("invalid Zenodo identifier")
        try:
            data = self._get(f"{config.ZENODO_API_URL}/{rid}")
        except NotFoundError:
            return None
        return _parse_record(parse_json(data, "Zenodo"))

    def get_open_access_pdf(self, identifier):
        r = self.get_metadata(identifier)
        return r.pdf_url if r else None
