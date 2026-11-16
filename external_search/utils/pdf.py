"""PDF helpers: signature check and a guarded download. No Telegram code here."""
import http.client
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Collection, Optional

from .. import config
from ..errors import (InvalidPdfError, PdfTooLargeError, PdfUnavailableError,
                      ProviderNetworkError, ProviderTimeout)

_CHUNK = 64 * 1024
_OK_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream",
             "binary/octet-stream"}
_MAX_REDIRECTS = 5


def is_pdf_bytes(data: bytes) -> bool:
    """Check the PDF magic header (validation of an already-authorized download)."""
    return data[:1024].lstrip().startswith(b"%PDF-")


def safe_filename(identifier: Optional[str], default: str = "paper") -> str:
    """Filesystem- and Telegram-safe '<id>.pdf' (ids like 'hep-th/9901001' contain '/')."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", identifier or "").strip("._") or default
    return base[:80] + ".pdf"


def host_allowed(host: str, allowed_hosts: Collection[str]) -> bool:
    """Exact match, or suffix match for entries that start with '.' (e.g. '.hal.science'
    allows 'theses.hal.science' but neither 'hal.science' itself nor 'evilhal.science')."""
    host = (host or "").lower()
    return any((host.endswith(h) and len(host) > len(h)) if h.startswith(".") else host == h
               for h in allowed_hosts)


def check_url(url: Optional[str], allowed_hosts: Collection[str]) -> str:
    """Return url if it is https on an allowed host; otherwise raise PdfUnavailableError."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme != "https" or not host_allowed(parts.hostname or "", allowed_hosts):
        raise PdfUnavailableError("PDF link is missing or not on an allowed host")
    return url


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to allowed https hosts."""
    max_redirections = _MAX_REDIRECTS

    def __init__(self, allowed_hosts):
        self._hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self._hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener(allowed_hosts) -> Callable:
    op = urllib.request.build_opener(_CheckedRedirects(allowed_hosts))

    def open_(url: str, timeout: float):
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT,
                                                   "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"})
        return op.open(req, timeout=timeout)
    return open_


def download_pdf(url: Optional[str], dest_dir: str, filename: str, *,
                 allowed_hosts: Collection[str],
                 timeout: Optional[float] = None, total_timeout: Optional[float] = None,
                 max_bytes: Optional[int] = None, opener: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic) -> str:
    """Download `url` into dest_dir/filename and return the path.

    Only https URLs on `allowed_hosts` (also after redirects) are fetched. The result
    must be a real PDF: HTTP 200, a non-HTML content type, a non-empty body within
    `max_bytes`, complete as far as Content-Length says, and starting with '%PDF-'.
    Any failure removes the partial file and raises a ProviderError subclass.
    """
    timeout = config.PDF_REQUEST_TIMEOUT if timeout is None else timeout
    total_timeout = config.PDF_TOTAL_TIMEOUT if total_timeout is None else total_timeout
    max_bytes = config.PDF_MAX_BYTES if max_bytes is None else max_bytes
    hosts = frozenset(h.lower() for h in allowed_hosts)
    check_url(url, hosts)
    open_ = opener or _default_opener(hosts)
    path = os.path.join(dest_dir, os.path.basename(filename))
    try:
        _fetch(open_, url, path, hosts, timeout, total_timeout, max_bytes, clock)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _fetch(open_, url, path, hosts, timeout, total_timeout, max_bytes, clock):
    deadline = clock() + total_timeout
    try:
        resp = open_(url, timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 410):
            raise PdfUnavailableError(f"PDF not available (HTTP {e.code})") from e
        raise ProviderNetworkError(f"PDF server returned HTTP {e.code}") from e
    except (socket.timeout, TimeoutError) as e:
        raise ProviderTimeout("PDF request timed out") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ProviderTimeout("PDF request timed out") from e
        raise ProviderNetworkError(f"PDF connection failed: {e.reason}") from e
    except OSError as e:
        raise ProviderNetworkError(f"PDF connection failed: {e}") from e

    with resp:
        status = getattr(resp, "status", None)
        if status is None and hasattr(resp, "getcode"):
            status = resp.getcode()
        if status != 200:
            raise PdfUnavailableError(f"unexpected HTTP status {status}")
        final = resp.geturl() if hasattr(resp, "geturl") else url
        check_url(final, hosts)
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in _OK_TYPES:
            raise InvalidPdfError(f"response is not a PDF (content type {ctype})")
        declared = None
        try:
            declared = int(resp.headers.get("Content-Length"))
        except (TypeError, ValueError):
            pass
        if declared is not None and declared > max_bytes:
            raise PdfTooLargeError(f"PDF is {declared} bytes (limit {max_bytes})")

        size, head = 0, b""
        try:
            with open(path, "wb") as out:
                while True:
                    if clock() > deadline:
                        raise ProviderTimeout("PDF download took too long")
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    if len(head) < 1024:
                        head += chunk[:1024 - len(head)]
                        if len(head) >= 1024 and not is_pdf_bytes(head):
                            raise InvalidPdfError("response does not have a PDF signature")
                    size += len(chunk)
                    if size > max_bytes:
                        raise PdfTooLargeError(f"PDF exceeds {max_bytes} bytes")
                    out.write(chunk)
        except (socket.timeout, TimeoutError) as e:
            raise ProviderTimeout("PDF download timed out") from e
        except http.client.IncompleteRead as e:
            raise InvalidPdfError("PDF download is incomplete") from e
        except OSError as e:
            raise ProviderNetworkError(f"PDF download interrupted: {e}") from e
    if size == 0:
        raise InvalidPdfError("PDF response is empty")
    if not is_pdf_bytes(head):
        raise InvalidPdfError("response does not have a PDF signature")
    if declared is not None and size != declared:
        raise InvalidPdfError("PDF download is incomplete")
