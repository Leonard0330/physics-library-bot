# external_search

Independent module; no Telegram imports. `bot.py` should only call `SearchManager`.

```python
from external_search import build_manager, SearchField
manager = build_manager()                       # providers from config.ENABLED_PROVIDERS
page = await manager.search("dark matter", SearchField.TITLE, page=1, page_size=10)
page.results, page.total, page.has_next, page.next_page, page.next_start, page.errors
```
- Empty result is not an error: `page.is_empty` is True.
- All providers failed -> `SearchFailedError` (`.errors` has the per-provider exceptions).
- Some failed -> results of the others plus `page.errors`.
- Error types: `InvalidQueryError`, `ProviderTimeout`, `ProviderNetworkError`, `ProviderResponseError`.

## Adding a provider
1. Create `providers/yourprovider.py` subclassing `BaseProvider`; set `name` and `capabilities`.
2. Implement only supported methods (`search(query, field, page, page_size) -> SearchPage`,
   `get_metadata`, `get_open_access_pdf`); others raise `UnsupportedOperation`.
3. Raise `ProviderError` subclasses for expected failures; return `SearchResult` objects.
4. Add it to `REGISTRY` in `providers/__init__.py` and enable via `EXT_SEARCH_PROVIDERS=arxiv,yours`.
5. Only expose `pdf_url` for legally free full text.

Config env vars: `EXT_SEARCH_TIMEOUT`, `EXT_SEARCH_EMAIL`, `EXT_SEARCH_PROVIDERS`.
The arXiv provider throttles itself to 1 request / 3 s per process (arXiv API terms).
Tests: `python -m unittest discover -s external_search/tests -t .` (run from project root).

## Telegram wiring
The package has no Telegram code. `external_search_ui.py` (project root) is the only Telegram layer;
`bot.py` just calls `ext_ui.init/start/handle_text/clear`. Callback namespace: `xs:`.
State (chosen search type, pagination context) is in memory only; no database tables are used.
Searches run in a small thread pool so slow external requests never block telebot's workers.
UI tests: `python -m unittest test_external_search_ui` (project root).

## Result details and PDF (phase 4)
- Result list has a number button per item (`xs:d:<token>:<page>:<idx>`) → details page (title, authors, date,
  abstract, id, source link) with `📄 Get PDF` (`xs:f:...`) and back (`xs:b:...`, served from the in-memory page cache, no new search).
- `manager.download_pdf(result, dest_dir)` downloads **only** `result.pdf_url` (the record's own arXiv link):
  https + host in `provider.pdf_hosts` (also after redirects), timeouts (`EXT_SEARCH_PDF_TIMEOUT`, `EXT_SEARCH_PDF_TOTAL_TIMEOUT`),
  size cap (`EXT_SEARCH_PDF_MAX_BYTES`, default 40 MB), HTTP 200, non-HTML content type, non-empty, complete, `%PDF-` signature.
- Errors: `PdfUnavailableError`, `InvalidPdfError`, `PdfTooLargeError` (+ `ProviderTimeout`/`ProviderNetworkError`); the UI maps each to a user message.
- The UI downloads into a `TemporaryDirectory`, sends with telebot `send_document`, and the directory is removed after sending or on any error.
- Tests (all HTTP mocked): `external_search/tests/test_pdf.py`, `test_external_search_ui.py`.
- Per-user PDF cooldown: `EXT_SEARCH_PDF_COOLDOWN` seconds (default 10), counted from each accepted request (failures included); in-memory only.

## Analytics (optional hook)
`external_search_ui.init(..., on_event=fn)` reports `ext_*` events (`fn(event, user_id, extra)`); bot.py wires it to
`analytics.log_event`. Events: `ext_search_submitted/completed/no_results/error`, `ext_details_viewed`,
`ext_pdf_requested/sent/failed`. They are separate from the local `search_*`/`file_*` events; query text is never stored.
`analytics.get_external_search_stats_in_period()` feeds the dashboard section `adm_anl:ext:<period>`.
Tests: `python -m unittest test_analytics_external` (stub database, in-memory SQLite).

## Providers: arXiv, Zenodo, HAL
`EXT_SEARCH_PROVIDERS` (default `arxiv,zenodo,hal`) selects the providers; a new provider is one file in `providers/` plus one line in `providers/__init__.py`.
- **Zenodo** (`providers/zenodo.py`): open-access publications with a PDF; PDF only from `zenodo.org`.
- **HAL** (`providers/hal.py`): open-access documents with a file; PDF only from `hal.science` / `hal.archives-ouvertes.fr` and their subdomains.
- `pdf_hosts` entries starting with `.` are suffix rules (`.hal.science` matches `theses.hal.science`, not `hal.science` or `evilhal.science`).
- With several providers the manager splits the page between them (`ceil(page_size / n)` each), interleaves the results and drops duplicates
  (same DOI, same arXiv id, or same normalized title of 20+ characters); a duplicate that has a PDF replaces one that has none.
  De-duplication works within a page, not across pages. Totals are the sum of the providers' totals.
- Fixtures in the Zenodo/HAL tests are modeled on the documented formats, not recorded from the live APIs: do one real search and one PDF per provider after deploying.

## NASA ADS (`providers/ads.py`)
Needs a free API token: set `ADS_API_TOKEN` (never commit it). Without it the provider is skipped (a warning is logged) and the other sources keep working.
ADS does not host PDFs: a record gets a PDF button only when ADS lists an arXiv e-print for it, and the PDF is then fetched from arXiv's hosts.
The token is sent only in the `Authorization` header, requests that carry it never follow redirects, and it is never logged or put in a URL.
Default ADS quota is 5000 requests/day per token; every search or page turn uses one request (`EXT_SEARCH_PROVIDERS` can drop `ads` to save quota).
Results from any provider that link to the same arXiv copy are de-duplicated (arXiv id taken from `arxiv.org/abs|pdf/<id>` links).
