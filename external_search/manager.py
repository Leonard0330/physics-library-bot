import asyncio
import logging
import math
import re
from typing import Dict, List, Optional

from . import config
from .base import BaseProvider, Capability
from .errors import PdfUnavailableError, ProviderError, SearchFailedError, UnsupportedOperation
from .models import AccessStatus, SearchField, SearchPage, SearchResult
from .utils import pdf as pdf_utils
from .utils.query import resolve_auto

log = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ARXIV_URL = re.compile(r"^https://(?:www\.)?arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-/]+?)(?:\.pdf)?$")


def _dup_keys(r: SearchResult) -> List[str]:
    """Identity keys used to drop the same paper returned by several providers."""
    keys = []
    if r.doi:
        doi = r.doi.strip().lower()
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi)
        keys.append("doi:" + doi)
    if r.source == "arxiv" and r.identifier:
        keys.append("arxiv:" + re.sub(r"v\d+$", "", r.identifier.lower()))
    for url in (r.pdf_url, r.landing_page_url):      # any provider linking to the arXiv copy
        m = _ARXIV_URL.search(url or "")
        if m:
            keys.append("arxiv:" + re.sub(r"v\d+$", "", m.group(1).lower()))
            break
    title = _NON_ALNUM.sub("", (r.title or "").lower())
    if len(title) >= 20:                     # short titles are too ambiguous to merge on
        keys.append("title:" + title)
    return keys


def _has_pdf(r: SearchResult) -> bool:
    return bool(r.pdf_url) and r.access_status != AccessStatus.CLOSED


def _merge(pages: List[List[SearchResult]]) -> List[SearchResult]:
    """Round-robin interleave (so every provider is represented), dropping duplicates.
    When a duplicate has a PDF and the kept record has none, the PDF-bearing one wins."""
    out: List[SearchResult] = []
    seen: Dict[str, int] = {}
    for i in range(max((len(p) for p in pages), default=0)):
        for res in pages:
            if i >= len(res):
                continue
            r = res[i]
            keys = _dup_keys(r)
            at = next((seen[k] for k in keys if k in seen), None)
            if at is None:
                at = len(out)
                out.append(r)
            elif _has_pdf(r) and not _has_pdf(out[at]):
                out[at] = r
            for k in keys:
                seen.setdefault(k, at)
    return out


class SearchManager:
    """Routes requests to registered providers. No provider-specific logic."""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}

    def register(self, provider: BaseProvider) -> None:
        if not provider.name:
            raise ValueError("Provider must define a name")
        if provider.name in self._providers:
            raise ValueError(f"Provider '{provider.name}' already registered")
        self._providers[provider.name] = provider

    def available(self, capability: Capability = Capability.NONE) -> List[str]:
        return [n for n, p in self._providers.items()
                if capability == Capability.NONE or p.supports(capability)]

    def _select(self, names: Optional[List[str]], cap: Capability) -> List[BaseProvider]:
        chosen = names if names is not None else self.available(cap)
        out = []
        for n in chosen:
            p = self._providers.get(n)
            if p is None:
                raise KeyError(f"Unknown provider '{n}'")
            if p.supports(cap):
                out.append(p)
        return out

    async def search(self, query: str, field: SearchField = SearchField.KEYWORD,
                     page: int = 1, page_size: int = config.DEFAULT_PAGE_SIZE,
                     providers: Optional[List[str]] = None) -> SearchPage:
        """Raises SearchFailedError if every selected provider fails; otherwise
        partial failures are reported in SearchPage.errors."""
        field = SearchField(field)
        if field == SearchField.AUTO:        # smart search: DOI / arXiv id / link / free text
            field, query = resolve_auto(query)
        capable = self._select(providers, Capability.SEARCH)
        if not capable:
            raise ProviderError("no search-capable provider available")
        selected = [p for p in capable if p.supports_field(field)]
        if not selected:                     # e.g. an arXiv id with only Zenodo ticked
            return SearchPage(results=[], page=page, page_size=page_size, total=0)
        # With several providers each one gets a share of the page, so the merged page stays
        # close to the requested size (the UI shows at most one row of number buttons).
        per = max(1, math.ceil(page_size / len(selected)))
        outcomes = await asyncio.gather(
            *(asyncio.to_thread(p.search, query, field, page, per) for p in selected),
            return_exceptions=True)
        pages, failures = [], {}
        for p, res in zip(selected, outcomes):
            if isinstance(res, ProviderError):
                failures[p.name] = res
            elif isinstance(res, Exception):
                log.error("Provider %s failed unexpectedly", p.name, exc_info=res)
                failures[p.name] = ProviderError("unexpected error")
            else:
                pages.append((p, res))
        if not pages:
            raise SearchFailedError(failures)
        for p, pg in pages:
            for r in pg.results:
                r.source = r.source or p.name
        results = _merge([pg.results for _, pg in pages])
        totals = [pg.total for _, pg in pages]
        return SearchPage(
            results=results, page=page,
            page_size=pages[0][1].page_size if len(selected) == 1 else per * len(selected),
            total=None if any(t is None for t in totals) else sum(totals),
            has_next=any(pg.has_next for _, pg in pages),
            errors={n: str(e) for n, e in failures.items()})

    async def get_metadata(self, identifier: str, provider: str) -> Optional[SearchResult]:
        return await self._single(provider, Capability.METADATA, "get_metadata", identifier)

    async def get_open_access_pdf(self, identifier: str, provider: str) -> Optional[str]:
        return await self._single(provider, Capability.OPEN_ACCESS_PDF,
                                  "get_open_access_pdf", identifier)

    async def download_pdf(self, result: SearchResult, dest_dir: str, *,
                           opener=None) -> str:
        """Download the PDF linked from this very record (`result.pdf_url`) into dest_dir.

        No other source or lookup is used. The URL must be https on a host the result's
        provider declares in `pdf_hosts`. Returns the file path; the caller owns dest_dir
        and is responsible for deleting it. Raises PdfError/ProviderError subclasses.
        """
        p = self._providers.get(result.source)
        if p is None:
            raise KeyError(f"Unknown provider '{result.source}'")
        if not p.supports(Capability.OPEN_ACCESS_PDF):
            raise UnsupportedOperation(f"{p.name} lacks OPEN_ACCESS_PDF")
        if result.access_status == AccessStatus.CLOSED or not result.pdf_url:
            raise PdfUnavailableError("record has no open PDF link")
        name = pdf_utils.safe_filename(result.identifier)
        return await asyncio.to_thread(
            pdf_utils.download_pdf, result.pdf_url, dest_dir, name,
            allowed_hosts=p.pdf_hosts, opener=opener)

    async def _single(self, provider, cap, method, identifier):
        p = self._providers.get(provider)
        if p is None:
            raise KeyError(f"Unknown provider '{provider}'")
        if not p.supports(cap):
            raise UnsupportedOperation(f"{provider} lacks {cap.name}")
        return await asyncio.to_thread(getattr(p, method), identifier)
