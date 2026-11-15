class ProviderError(Exception):
    """Base class for expected provider failures."""


class UnsupportedOperation(ProviderError):
    pass


class InvalidQueryError(ProviderError):
    """Bad user input (empty query, bad page number, bad identifier)."""


class ProviderTimeout(ProviderError):
    pass


class ProviderNetworkError(ProviderError):
    """Connection failure or non-success HTTP status (incl. rate limiting)."""


class ProviderResponseError(ProviderError):
    """Response received but malformed or reporting an error."""


class PdfError(ProviderError):
    """Base class for PDF download/validation failures."""


class PdfUnavailableError(PdfError):
    """No usable PDF link, or the server says the file is not there (404, 403, ...)."""


class InvalidPdfError(PdfError):
    """Response is not a genuine PDF (HTML page, empty, bad signature, truncated)."""


class PdfTooLargeError(PdfError):
    pass


class SearchFailedError(ProviderError):
    """Every selected provider failed. `errors` maps provider name -> exception."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{n}: {e}" for n, e in errors.items()) or "search failed")
