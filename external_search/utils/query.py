"""Pure input classification for the smart search box (no network, no Telegram)."""
import re
from typing import Tuple

from ..errors import InvalidQueryError
from ..models import SearchField
from .doi import doi_for_query

_URLISH = re.compile(r"^(?:https?://|www\.|(?:dx\.)?doi\.org/|arxiv\.org/)", re.I)


def classify_query(text: str) -> Tuple[str, str]:
    """Return (kind, normalized): kind is 'doi', 'arxiv', 'url' (a link we cannot use) or 'text'."""
    from ..providers.arxiv import normalize_arxiv_id     # lazy: avoids an import cycle
    s = " ".join((text or "").split())
    if not s:
        return "text", ""
    is_url = bool(_URLISH.match(s)) and " " not in s
    if is_url:
        s = s.split("#")[0].split("?")[0]
    if is_url or " " not in s:
        arx = normalize_arxiv_id(s)
        if arx:
            return "arxiv", arx
    doi = doi_for_query(s)
    if doi:
        return "doi", doi
    if is_url:
        return "url", s
    return "text", s


def resolve_auto(text: str) -> Tuple[SearchField, str]:
    """Map free input to the (field, query) a provider should receive."""
    kind, norm = classify_query(text)
    if kind == "doi":
        return SearchField.DOI, norm
    if kind == "arxiv":
        return SearchField.ID, norm
    if kind == "url":
        raise InvalidQueryError("unsupported link")
    return SearchField.AUTO, norm
