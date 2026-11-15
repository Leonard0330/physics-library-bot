from typing import Iterable, Optional

from . import config
from .manager import SearchManager
from .providers import REGISTRY


def build_manager(names: Optional[Iterable[str]] = None) -> SearchManager:
    """Create a SearchManager with the configured (or given) providers registered."""
    m = SearchManager()
    for n in (names if names is not None else config.ENABLED_PROVIDERS):
        if n not in REGISTRY:
            raise KeyError(f"Unknown provider '{n}'")
        m.register(REGISTRY[n]())
    return m
