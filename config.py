"""Configuration for external search. No Telegram code here."""
import os

REQUEST_TIMEOUT = float(os.getenv("EXT_SEARCH_TIMEOUT", "10"))
USER_AGENT = "PhysicsLibraryBot/1.0 (mailto:%s)" % os.getenv("EXT_SEARCH_EMAIL", "contact@example.com")
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
ENABLED_PROVIDERS = [p for p in os.getenv("EXT_SEARCH_PROVIDERS", "arxiv").split(",") if p]

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_MIN_INTERVAL = 3.0       # arXiv API terms: at most 1 request / 3 s
ARXIV_MAX_RESPONSE_BYTES = 5_000_000

# PDF download (only from the arXiv record's own PDF link)
PDF_REQUEST_TIMEOUT = float(os.getenv("EXT_SEARCH_PDF_TIMEOUT", "20"))        # per socket operation
PDF_TOTAL_TIMEOUT = float(os.getenv("EXT_SEARCH_PDF_TOTAL_TIMEOUT", "60"))    # whole download
PDF_MAX_BYTES = int(os.getenv("EXT_SEARCH_PDF_MAX_BYTES", str(40 * 1024 * 1024)))  # Telegram bots: 50 MB
PDF_USER_COOLDOWN = float(os.getenv("EXT_SEARCH_PDF_COOLDOWN", "10"))   # seconds between PDF requests per user
