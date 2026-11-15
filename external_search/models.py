from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


class AccessStatus(str, Enum):
    OPEN = "open"          # legally free full text available
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SearchField(str, Enum):
    TITLE = "title"
    AUTHOR = "author"
    KEYWORD = "keyword"


@dataclass
class SearchResult:
    title: str
    source: str
    identifier: Optional[str] = None        # provider-native id, e.g. "2101.00001v2"
    authors: List[str] = field(default_factory=list)
    abstract: Optional[str] = None
    published: Optional[date] = None
    publication_year: Optional[int] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    landing_page_url: Optional[str] = None
    pdf_url: Optional[str] = None
    access_status: AccessStatus = AccessStatus.UNKNOWN


@dataclass
class SearchPage:
    results: List[SearchResult]
    page: int = 1                       # 1-based
    page_size: int = 10
    total: Optional[int] = None         # total matches, if provider reports it
    has_next: bool = False
    errors: Dict[str, str] = field(default_factory=dict)  # provider -> message (partial failures)

    @property
    def start(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def next_page(self) -> Optional[int]:
        return self.page + 1 if self.has_next else None

    @property
    def next_start(self) -> Optional[int]:
        return self.start + self.page_size if self.has_next else None

    @property
    def is_empty(self) -> bool:
        return not self.results
