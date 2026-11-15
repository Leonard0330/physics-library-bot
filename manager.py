import asyncio
import logging
from typing import Dict, List, Optional

from . import config
from .base import BaseProvider, Capability
from .errors import PdfUnavailableError, ProviderError, SearchFailedError, UnsupportedOperation
from .models import AccessStatus, SearchField, SearchPage, SearchResult
from .utils import pdf as pdf_utils

log = logging.getLogger(__name__)


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
        selected = self._select(providers, Capability.SEARCH)
        if not selected:
            raise ProviderError("no search-capable provider available")
        outcomes = await asyncio.gather(
            *(asyncio.to_thread(p.search, query, field, page, page_size) for p in selected),
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
        results: List[SearchResult] = []
        for p, pg in pages:
            for r in pg.results:
                r.source = r.source or p.name
                results.append(r)
        totals = [pg.total for _, pg in pages]
        return SearchPage(
            results=results, page=page, page_size=pages[0][1].page_size,
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
