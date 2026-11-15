import re
from typing import Optional

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)


def extract_doi(text: str) -> Optional[str]:
    """Extract a DOI from a bare DOI or doi.org URL; returns lowercase or None."""
    m = _DOI_RE.search(text or "")
    return m.group(0).rstrip(".,;)").lower() if m else None
