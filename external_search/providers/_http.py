"""Shared helpers for JSON providers (Zenodo, HAL). No Telegram code here."""
import html
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from .. import config
from ..errors import ProviderNetworkError, ProviderResponseError, ProviderTimeout


class NotFoundError(ProviderNetworkError):
    """HTTP 404 from the provider."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: an Authorization header must not be forwarded to other hosts."""

    def redirect_request(self, *a, **k):
        return None


def _build_opener(authenticated: bool):
    return urllib.request.build_opener(_NoRedirect()) if authenticated else urllib.request.build_opener()


def http_get(url: str, timeout: float, max_bytes: int, label: str, headers: Optional[dict] = None) -> bytes:
    """GET a JSON resource. Requests carrying extra headers (tokens) never follow redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT,
                                               "Accept": "application/json", **(headers or {})})
    try:
        with _build_opener(bool(headers)).open(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        cls = NotFoundError if e.code == 404 else ProviderNetworkError
        raise cls(f"{label} returned HTTP {e.code}") from e
    except (socket.timeout, TimeoutError) as e:
        raise ProviderTimeout(f"{label} request timed out") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ProviderTimeout(f"{label} request timed out") from e
        raise ProviderNetworkError(f"{label} connection failed: {e.reason}") from e
    except OSError as e:
        raise ProviderNetworkError(f"{label} connection failed: {e}") from e
    if len(data) > max_bytes:
        raise ProviderResponseError(f"{label} response too large")
    return data


def parse_json(data: bytes, label: str) -> dict:
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise ProviderResponseError(f"{label} returned invalid JSON") from e
    if not isinstance(obj, dict):
        raise ProviderResponseError(f"unexpected {label} response format")
    return obj


_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: Optional[str]) -> Optional[str]:
    """Strip HTML tags/entities and collapse whitespace; None when nothing is left."""
    if not s:
        return None
    return " ".join(html.unescape(_TAG_RE.sub(" ", s)).split()) or None


class Throttle:
    """Serialize calls so a provider's request-rate policy is honoured."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._min, self._clock, self._sleep = min_interval, clock, sleep
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                delay = self._last + self._min - self._clock()
                if delay > 0:
                    self._sleep(delay)
            self._last = self._clock()
