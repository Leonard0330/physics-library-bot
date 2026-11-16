"""Configuration for external search. No Telegram code here."""
import os

REQUEST_TIMEOUT = float(os.getenv("EXT_SEARCH_TIMEOUT", "10"))
USER_AGENT = "PhysicsLibraryBot/1.0 (mailto:%s)" % os.getenv("EXT_SEARCH_EMAIL", "contact@example.com")
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
ENABLED_PROVIDERS = [p for p in os.getenv("EXT_SEARCH_PROVIDERS", "arxiv,zenodo,hal,ads").split(",") if p]

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_MIN_INTERVAL = 3.0       # arXiv API terms: at most 1 request / 3 s
ARXIV_MAX_RESPONSE_BYTES = 5_000_000

ZENODO_API_URL = "https://zenodo.org/api/records"
ZENODO_MIN_INTERVAL = 2.0      # Zenodo: 30 requests/minute
ZENODO_MAX_RESPONSE_BYTES = 5_000_000

HAL_API_URL = "https://api.archives-ouvertes.fr/search/"
HAL_MIN_INTERVAL = 0.5
HAL_MAX_RESPONSE_BYTES = 5_000_000

ADS_API_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_API_TOKEN = os.getenv("ADS_API_TOKEN", "")   # required; provider is skipped when empty
ADS_MIN_INTERVAL = 0.3                           # ADS default quota: 5000 requests/day
ADS_MAX_RESPONSE_BYTES = 5_000_000

# PDF download (only from the record's own PDF link, on the provider's declared hosts)
PDF_REQUEST_TIMEOUT = float(os.getenv("EXT_SEARCH_PDF_TIMEOUT", "20"))        # per socket operation
PDF_TOTAL_TIMEOUT = float(os.getenv("EXT_SEARCH_PDF_TOTAL_TIMEOUT", "60"))    # whole download
PDF_MAX_BYTES = int(os.getenv("EXT_SEARCH_PDF_MAX_BYTES", str(40 * 1024 * 1024)))  # Telegram bots: 50 MB
PDF_USER_COOLDOWN = float(os.getenv("EXT_SEARCH_PDF_COOLDOWN", "10"))   # seconds between PDF requests per user
