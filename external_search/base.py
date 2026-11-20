from abc import ABC
from enum import Flag, auto
from typing import Optional

from .errors import (InvalidQueryError, ProviderError, ProviderNetworkError,  # noqa: F401
                     ProviderResponseError, ProviderTimeout, UnsupportedOperation)
from .models import SearchField, SearchPage, SearchResult


class Capability(Flag):
    NONE = 0
    SEARCH = auto()
    METADATA = auto()
    OPEN_ACCESS_PDF = auto()


class BaseProvider(ABC):
    """Common provider interface. No Telegram code in providers.

    Methods are synchronous; the manager runs them in worker threads.
    Raise ProviderError subclasses for expected failures.
    """

    name: str = ""
    capabilities: Capability = Capability.NONE
    # Hosts from which this provider's own record links may be downloaded as PDF.
    pdf_hosts: frozenset = frozenset()
    # Search fields this provider can serve; the manager skips it for any other field.
    search_fields: frozenset = frozenset({SearchField.TITLE, SearchField.AUTHOR,
                                          SearchField.KEYWORD, SearchField.AUTO})

    @classmethod
    def configured(cls) -> bool:
        """False when a required setting (e.g. an API token) is missing; the factory then
        skips the provider instead of failing."""
        return True

    def supports(self, cap: Capability) -> bool:
        return bool(self.capabilities & cap)

    def supports_field(self, field: SearchField) -> bool:
        return field in self.search_fields

    def search(self, query: str, field: SearchField = SearchField.KEYWORD,
               page: int = 1, page_size: int = 10) -> SearchPage:
        raise UnsupportedOperation(f"{self.name}: search not supported")

    def get_metadata(self, identifier: str) -> Optional[SearchResult]:
        raise UnsupportedOperation(f"{self.name}: metadata not supported")

    def get_open_access_pdf(self, identifier: str) -> Optional[str]:
        """URL of a legally free PDF, or None."""
        raise UnsupportedOperation(f"{self.name}: open-access PDF not supported")
