"""Telegram UI for External Search.

This is the only place that connects the independent `external_search` package to
Telegram. The package itself stays Telegram-free. bot.py calls into this module
through a few hooks (see init/start/handle_text/clear); no provider-specific logic
lives here.

Callback data namespace: ``xs:`` (``xs:t:<field>``, ``xs:p:<token>:<page>``,
``xs:d:<token>:<page>:<idx>`` details, ``xs:b:<token>:<page>`` back to the list,
``xs:f:<token>:<page>:<idx>`` send PDF, ``xs:n`` no-op, ``xs:new``).
Usage events go through an injected ``on_event(event, user_id, extra)`` hook (bot.py wires it to
analytics); they use their own ``ext_*`` names and never the local ``search_*``/``file_*`` events.
Query text is never reported, only field, counts and error kinds.
State (including the shown result pages) is in memory only; nothing is stored in the DB.
PDFs are downloaded by the package (record's own PDF link only) into a temporary
directory that is always removed after sending or on error.
"""
import asyncio
import html
import itertools
import logging
import math
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

from telebot import types

from external_search import (AccessStatus, Capability, InvalidPdfError, InvalidQueryError,
                             PdfTooLargeError, PdfUnavailableError, ProviderNetworkError,
                             ProviderResponseError, ProviderTimeout, SearchFailedError,
                             SearchField, SearchManager, SearchPage, build_manager)
from external_search import config as xs_config
from external_search.utils.pdf import safe_filename

log = logging.getLogger(__name__)

PREFIX = "xs:"
PAGE_SIZE = 5
MAX_QUERY_LEN = 200
CTX_MAX = 500
COOLDOWN_MAX_USERS = 5000   # bound on remembered per-user cooldown timestamps
PAGES_MAX = 300           # cached result pages (needed for details/back without a new search)
ABSTRACT_MAX = 2500
DETAIL_AUTHORS_MAX = 12
SAFE_LEN = 3900            # Telegram limit is 4096; keep a margin
SIGNATURE = "\n\n@PhysisLib_Bot"      # same stamp bot.py puts under every library file
CANCEL_LABELS = {"❌ لغو", "❌ Cancel"}

BUTTON_LABEL = {"fa": "🌍 جست‌وجوی خارجی", "en": "🌍 External Search"}

PROVIDER_LABELS = {"arxiv": "arXiv", "zenodo": "Zenodo", "hal": "HAL"}     # display names only; fallback is the provider name

FIELD_LABELS = {
    SearchField.TITLE:   {"fa": "📖 عنوان",    "en": "📖 Title"},
    SearchField.AUTHOR:  {"fa": "👤 نویسنده",  "en": "👤 Author"},
    SearchField.KEYWORD: {"fa": "🔑 کلیدواژه", "en": "🔑 Keywords"},
}

TX = {
    "pick": {
        "fa": "🌍 <b>جست‌وجوی خارجی</b>\nمنابع: {sources}\nاین جست‌وجو جدا از کتابخانه‌ی محلی است.\n\nنوع جست‌وجو را انتخاب کن:",
        "en": "🌍 <b>External Search</b>\nSources: {sources}\nThis is separate from the local library search.\n\nChoose the search type:",
    },
    "ask_title": {
        "fa": "📖 عنوان مقاله را وارد کن.\nعبارت <b>دقیقاً به همان ترتیب</b> در عنوان جست‌وجو می‌شود.\nℹ️ محتوای این منابع عمدتاً انگلیسی است؛ عبارت را به انگلیسی بنویس.",
        "en": "📖 Enter the paper title.\nThe phrase is matched <b>exactly, in that word order</b>, within titles.\nℹ️ Content is mostly English; type your query in English.",
    },
    "ask_author": {
        "fa": "👤 نام نویسنده را وارد کن (مثلاً Albert Einstein).\nنام به‌صورت یک عبارت در فهرست نویسندگان جست‌وجو می‌شود.\nℹ️ نام را به انگلیسی بنویس.",
        "en": "👤 Enter the author name (e.g. Albert Einstein).\nThe name is matched as a phrase in the author list.\nℹ️ Type the name in English.",
    },
    "ask_keyword": {
        "fa": "🔑 کلیدواژه‌ها را وارد کن (مثلاً dark matter halo).\nفقط مقاله‌هایی نمایش داده می‌شوند که <b>همه‌ی کلمه‌ها</b> را داشته باشند (در عنوان، چکیده و سایر فیلدها).\nℹ️ کلیدواژه‌ها را به انگلیسی بنویس.",
        "en": "🔑 Enter keywords (e.g. dark matter halo).\nOnly papers containing <b>all words</b> are shown (title, abstract and other fields).\nℹ️ Type keywords in English.",
    },
    "searching": {"fa": "⏳ در حال جست‌وجو…", "en": "⏳ Searching…"},
    "header": {
        "fa": "🌍 <b>{field}</b>: «{query}»\n📊 {first}–{last} از {total} نتیجه · صفحه {page}/{pages}",
        "en": "🌍 <b>{field}</b>: “{query}”\n📊 {first}–{last} of {total} results · page {page}/{pages}",
    },
    "header_unknown_total": {
        "fa": "🌍 <b>{field}</b>: «{query}»\n📊 صفحه {page}",
        "en": "🌍 <b>{field}</b>: “{query}”\n📊 Page {page}",
    },
    "source": {"fa": "📚 منبع", "en": "📚 Source"},
    "authors_more": {"fa": "و دیگران", "en": "et al."},
    "no_results": {
        "fa": "🔎 نتیجه‌ای پیدا نشد. عبارت را ساده‌تر یا به انگلیسی بنویس، یا نوع جست‌وجو را عوض کن.",
        "en": "🔎 No results found. Try simpler or English terms, or another search type.",
    },
    "no_more": {"fa": "🔎 نتیجه‌ی بیشتری در این صفحه نیست.", "en": "🔎 No more results on this page."},
    "invalid": {
        "fa": "❗️ عبارت معتبر نیست. حداقل یک حرف یا عدد وارد کن:",
        "en": "❗️ Invalid query. Enter at least one letter or digit:",
    },
    "too_long": {
        "fa": "❗️ عبارت خیلی طولانی است (حداکثر {n} نویسه). کوتاه‌تر بنویس:",
        "en": "❗️ Query too long (max {n} characters). Please shorten it:",
    },
    "err_timeout": {
        "fa": "⚠️ پاسخی از سرویس جست‌وجو نرسید (timeout). کمی بعد دوباره امتحان کن.",
        "en": "⚠️ The search service did not respond in time. Please try again shortly.",
    },
    "err_network": {
        "fa": "⚠️ اتصال به سرویس جست‌وجو برقرار نشد یا سرویس شلوغ است. کمی بعد دوباره امتحان کن.",
        "en": "⚠️ Could not reach the search service, or it is busy. Please try again shortly.",
    },
    "err_response": {
        "fa": "⚠️ سرویس جست‌وجو پاسخ نامعتبری داد. دوباره امتحان کن.",
        "en": "⚠️ The search service returned an invalid response. Please try again.",
    },
    "err_generic": {
        "fa": "⚠️ خطایی در جست‌وجوی خارجی رخ داد. دوباره امتحان کن.",
        "en": "⚠️ Something went wrong with the external search. Please try again.",
    },
    "expired": {
        "fa": "⏳ این جست‌وجو منقضی شده است. دوباره جست‌وجو کن.",
        "en": "⏳ This search has expired. Please search again.",
    },
    "cancelled": {"fa": "↩️ لغو شد.", "en": "↩️ Cancelled."},
    "new_search": {"fa": "🌍 جست‌وجوی جدید", "en": "🌍 New search"},
    "hint_details": {
        "fa": "ℹ️ برای دیدن جزئیات و دریافت PDF، شماره‌ی نتیجه را بزن.",
        "en": "ℹ️ Tap a result number for details and the PDF.",
    },
    "d_authors": {"fa": "👤 <b>نویسندگان</b>", "en": "👤 <b>Authors</b>"},
    "d_date": {"fa": "📅 <b>تاریخ</b>", "en": "📅 <b>Date</b>"},
    "d_id": {"fa": "🆔 <b>شناسه</b>", "en": "🆔 <b>ID</b>"},
    "d_link": {"fa": "🔗 <b>لینک منبع</b>", "en": "🔗 <b>Source link</b>"},
    "d_abstract": {"fa": "📝 <b>چکیده</b>", "en": "📝 <b>Abstract</b>"},
    "d_no_abstract": {"fa": "چکیده‌ای موجود نیست.", "en": "No abstract available."},
    "btn_pdf": {"fa": "📄 دریافت PDF", "en": "📄 Get PDF"},
    "btn_back": {"fa": "◀️ بازگشت به نتایج", "en": "◀️ Back to results"},
    "pdf_wait": {"fa": "⏳ در حال دریافت PDF…", "en": "⏳ Fetching the PDF…"},
    "pdf_cooldown": {
        "fa": "⏳ چند ثانیه صبر کن و دوباره PDF بخواه ({n} ثانیه).",
        "en": "⏳ Please wait {n} s before requesting another PDF.",
    },
    "pdf_busy": {"fa": "⏳ دریافت این PDF در حال انجام است.", "en": "⏳ This PDF is already being fetched."},
    "pdf_unavailable": {
        "fa": "⚠️ PDF این مقاله در منبع در دسترس نیست.",
        "en": "⚠️ The PDF of this paper is not available at its source.",
    },
    "pdf_too_large": {
        "fa": "⚠️ حجم این PDF بیشتر از حد مجاز ارسال است؛ از لینک منبع دریافتش کن.",
        "en": "⚠️ This PDF is larger than the allowed size; please open it from the source link.",
    },
    "pdf_invalid": {
        "fa": "⚠️ فایل دریافتی PDF معتبر نبود، بنابراین ارسال نشد. بعداً دوباره امتحان کن.",
        "en": "⚠️ The downloaded file was not a valid PDF, so it was not sent. Please try again later.",
    },
    "pdf_timeout": {
        "fa": "⚠️ دریافت PDF بیش از حد طول کشید (timeout). دوباره امتحان کن.",
        "en": "⚠️ Fetching the PDF timed out. Please try again.",
    },
    "pdf_network": {
        "fa": "⚠️ اتصال به منبع PDF برقرار نشد یا سرویس شلوغ است. کمی بعد دوباره امتحان کن.",
        "en": "⚠️ Could not reach the PDF source, or it is busy. Please try again shortly.",
    },
    "pdf_send_failed": {
        "fa": "⚠️ PDF دریافت شد ولی ارسال آن در تلگرام ناموفق بود. دوباره امتحان کن.",
        "en": "⚠️ The PDF was fetched but sending it on Telegram failed. Please try again.",
    },
    "pdf_generic": {
        "fa": "⚠️ خطایی در دریافت PDF رخ داد. دوباره امتحان کن.",
        "en": "⚠️ Something went wrong while fetching the PDF. Please try again.",
    },
    "unavailable": {
        "fa": "⚠️ جست‌وجوی خارجی در حال حاضر در دسترس نیست.",
        "en": "⚠️ External search is currently unavailable.",
    },
}

# ── module state (in memory only) ───────────────────────────────────────────
_bot = None
_get_lang: Callable = None
_main_keyboard: Callable = None
_cancel_keyboard: Callable = None
_is_menu_label: Callable[[str], bool] = lambda s: False
_manager: Optional[SearchManager] = None
_submit: Callable = None
_on_event: Optional[Callable] = None
_pdf_submit: Callable = None       # separate pool: slow PDF downloads must not starve searches

_waiting: Dict[int, SearchField] = {}                 # uid -> chosen field, awaiting query text
_ctx: "OrderedDict[str, tuple]" = OrderedDict()       # token -> (query, field value)
_ctx_lock = threading.Lock()
_ctx_counter = itertools.count(1)
# Per-process random prefix: after a restart, buttons of old messages cannot match another
# user's fresh context (the counter alone would restart at 1).
_BOOT = secrets.token_hex(2)
# (token, page) -> (SearchPage, query, field value); lets details/back work without a new search
_pages: "OrderedDict[tuple, tuple]" = OrderedDict()
_pdf_last: Dict[int, float] = {}                    # uid -> time of last accepted PDF request
_clock: Callable[[], float] = time.monotonic
_pdf_busy = set()                                     # (uid, token, page, idx) being fetched


def init(bot, get_lang: Callable, main_keyboard: Callable, cancel_keyboard: Callable,
         is_menu_label: Optional[Callable[[str], bool]] = None,
         manager: Optional[SearchManager] = None, submit: Optional[Callable] = None,
         pdf_submit: Optional[Callable] = None, on_event: Optional[Callable] = None) -> None:
    """Wire the module to the bot (dependency injection avoids importing bot.py)."""
    global _bot, _get_lang, _main_keyboard, _cancel_keyboard, _is_menu_label, _manager, _submit, _pdf_submit, _on_event
    _bot, _get_lang = bot, get_lang
    _main_keyboard, _cancel_keyboard = main_keyboard, cancel_keyboard
    _is_menu_label = is_menu_label or (lambda s: False)
    _manager = manager
    _on_event = on_event
    _submit = submit or ThreadPoolExecutor(max_workers=4, thread_name_prefix="xs").submit
    _pdf_submit = pdf_submit or (_submit if submit else
                                 ThreadPoolExecutor(max_workers=2, thread_name_prefix="xs-pdf").submit)
    _waiting.clear()
    with _ctx_lock:
        _ctx.clear()
        _pages.clear()
        _pdf_busy.clear()
        _pdf_last.clear()
    bot.callback_query_handler(func=lambda c: (c.data or "").startswith(PREFIX))(_callback)


def _mgr() -> SearchManager:
    global _manager
    if _manager is None:
        _manager = build_manager()
    return _manager


def _emit(event: str, user, **extra) -> None:
    """Report a usage event; analytics problems must never affect the user flow."""
    if _on_event is None:
        return
    try:
        _on_event(event, user.id, extra or None)
    except Exception:  # noqa: BLE001
        log.warning("external search: on_event failed (event=%s)", event, exc_info=True)


def _tx(key: str, lang: str, **kw) -> str:
    s = TX[key][lang if lang in ("fa", "en") else "en"]
    return s.format(**kw) if kw else s


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _source_label(name: str) -> str:
    return PROVIDER_LABELS.get(name, name)


# ── state API used by bot.py ────────────────────────────────────────────────
def clear(uid: int) -> None:
    _waiting.pop(uid, None)


def is_waiting(uid: int) -> bool:
    return uid in _waiting


def start(chat_id: int, user) -> None:
    """Entry point from the main-menu button: show the search-type picker."""
    clear(user.id)
    lang = _get_lang(user)
    try:
        names = _mgr().available(Capability.SEARCH)
    except Exception:
        log.exception("external search manager unavailable")
        names = []
    if not names:
        _bot.send_message(chat_id, _tx("unavailable", lang), reply_markup=_main_keyboard(user))
        return
    _bot.send_message(chat_id, _tx("pick", lang, sources=_e(", ".join(_source_label(n) for n in names))),
                      parse_mode="HTML", reply_markup=_type_keyboard(lang))


def _type_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.row(*[types.InlineKeyboardButton(FIELD_LABELS[f][lang], callback_data=f"{PREFIX}t:{f.value}")
             for f in (SearchField.TITLE, SearchField.AUTHOR, SearchField.KEYWORD)])
    return mk


def handle_text(message) -> bool:
    """Called by bot.py's text handler. Returns True if the message was consumed."""
    user = message.from_user
    uid = user.id
    field = _waiting.get(uid)
    if field is None:
        return False
    text = (message.text or "").strip()
    lang = _get_lang(user)
    chat_id = message.chat.id
    if text in CANCEL_LABELS:
        clear(uid)
        _bot.send_message(chat_id, _tx("cancelled", lang), reply_markup=_main_keyboard(user))
        return True
    if _is_menu_label(text):
        clear(uid)           # another menu button: leave this flow, let bot.py handle it
        return False
    if len(text) > MAX_QUERY_LEN:
        _bot.send_message(chat_id, _tx("too_long", lang, n=MAX_QUERY_LEN), reply_markup=_cancel_keyboard(user))
        return True
    if not any(ch.isalnum() for ch in text):
        _bot.send_message(chat_id, _tx("invalid", lang), reply_markup=_cancel_keyboard(user))
        return True
    clear(uid)
    _emit("ext_search_submitted", user, field=field.value)
    token = _ctx_put(text, field)
    # Restores the main reply keyboard; results then replace this placeholder via edit.
    placeholder = _bot.send_message(chat_id, _tx("searching", lang), reply_markup=_main_keyboard(user))
    _submit(_search_job, chat_id, user, token, text, field, 1, placeholder.message_id, True)
    return True


# ── callbacks ───────────────────────────────────────────────────────────────
def _callback(c) -> None:
    user = c.from_user
    lang = _get_lang(user)
    parts = (c.data or "").split(":")
    try:
        action = parts[1]
        if action == "t" and len(parts) == 3:
            field = SearchField(parts[2])
            _bot.answer_callback_query(c.id)
            _waiting[user.id] = field
            _bot.send_message(c.message.chat.id, _tx(f"ask_{field.value}", lang),
                              parse_mode="HTML", reply_markup=_cancel_keyboard(user))
        elif action == "p" and len(parts) == 4:
            page = int(parts[3])
            ctx = _ctx_get(parts[2])
            if page < 1:
                raise ValueError("bad page")
            if ctx is None:
                _bot.answer_callback_query(c.id, _tx("expired", lang), show_alert=True)
                return
            _bot.answer_callback_query(c.id)
            _submit(_search_job, c.message.chat.id, user, parts[2], ctx[0], SearchField(ctx[1]),
                    page, c.message.message_id, False)
        elif action in ("d", "f") and len(parts) == 5:
            page, idx = int(parts[3]), int(parts[4])
            entry = _pages_get(parts[2], page)
            if entry is None:
                _bot.answer_callback_query(c.id, _tx("expired", lang), show_alert=True)
                return
            if not 0 <= idx < len(entry[0].results):
                raise ValueError("bad index")
            r = entry[0].results[idx]
            if action == "d":
                _bot.answer_callback_query(c.id)
                _emit("ext_details_viewed", user, source=r.source)
                _show(c.message.chat.id, c.message.message_id, format_details(r, lang),
                      _details_markup(parts[2], page, idx, r, lang))
            else:
                _start_pdf(c, user, lang, parts[2], page, idx, r)
        elif action == "b" and len(parts) == 4:
            page = int(parts[3])
            entry = _pages_get(parts[2], page)
            if entry is None:
                _bot.answer_callback_query(c.id, _tx("expired", lang), show_alert=True)
                return
            _bot.answer_callback_query(c.id)
            pg, query, field = entry
            _show(c.message.chat.id, c.message.message_id,
                  format_page(pg, query, SearchField(field), lang),
                  _page_markup(parts[2], pg, lang))
        elif action == "n":
            _bot.answer_callback_query(c.id)
        elif action == "new":
            _bot.answer_callback_query(c.id)
            start(c.message.chat.id, user)
        else:
            raise ValueError("unknown action")
    except ValueError:
        _bot.answer_callback_query(c.id)


def _ctx_put(query: str, field: SearchField) -> str:
    with _ctx_lock:
        tok = _BOOT + format(next(_ctx_counter), "x")
        _ctx[tok] = (query, field.value)
        while len(_ctx) > CTX_MAX:
            _ctx.popitem(last=False)
        return tok


def _pages_put(token: str, page: SearchPage, query: str, field: SearchField) -> None:
    with _ctx_lock:
        _pages[(token, page.page)] = (page, query, field.value)
        _pages.move_to_end((token, page.page))
        while len(_pages) > PAGES_MAX:
            _pages.popitem(last=False)


def _pages_get(token: str, page: int):
    with _ctx_lock:
        return _pages.get((token, page))


def _ctx_get(token: str):
    with _ctx_lock:
        return _ctx.get(token)


# ── search job (runs in worker thread; never blocks telebot handlers) ──────
def _search_job(chat_id: int, user, token: str, query: str, field: SearchField,
                page: int, message_id: int, is_new: bool) -> None:
    lang = _get_lang(user)
    try:
        result = asyncio.run(_mgr().search(query, field, page, PAGE_SIZE))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a user message
        _report_error(chat_id, user, lang, exc, message_id, field, is_new)
        return
    try:
        if is_new:
            _emit("ext_search_no_results" if result.is_empty else "ext_search_completed", user,
                  field=field.value, **({} if result.is_empty else {"result_count": result.total or len(result.results)}))
        if result.is_empty:
            if page == 1:
                _show(chat_id, message_id, _tx("no_results", lang), _new_search_markup(lang))
            else:   # keep the current results (and their buttons) on screen
                _bot.send_message(chat_id, _tx("no_more", lang))
            return
        _pages_put(token, result, query, field)
        text = format_page(result, query, field, lang)
        _show(chat_id, message_id, text, _page_markup(token, result, lang))
    except Exception:
        log.exception("external search: failed to display results")
        _bot.send_message(chat_id, _tx("err_generic", lang))


def _show(chat_id: int, message_id: int, text: str, markup) -> None:
    """Edit the given message in place; fall back to a new message if editing fails."""
    try:
        _bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML",
                               disable_web_page_preview=True, reply_markup=markup)
    except Exception as e:  # noqa: BLE001
        if "message is not modified" in str(e):
            return
        log.warning("edit failed (%s); sending a new message", e)
        _bot.send_message(chat_id, text, parse_mode="HTML",
                          disable_web_page_preview=True, reply_markup=markup)


def _report_error(chat_id, user, lang, exc, message_id, field, is_new) -> None:
    errs = list(exc.errors.values()) if isinstance(exc, SearchFailedError) else [exc]
    if any(isinstance(x, InvalidQueryError) for x in errs):
        key = "invalid"
    elif any(isinstance(x, ProviderTimeout) for x in errs):
        key = "err_timeout"
    elif any(isinstance(x, ProviderNetworkError) for x in errs):
        key = "err_network"
    elif any(isinstance(x, ProviderResponseError) for x in errs):
        key = "err_response"
    else:
        key = "err_generic"
    if key != "invalid":    # bad input is not a technical error
        _emit("ext_search_error", user, field=field.value, kind=key, paged=not is_new)
    if key == "err_generic" and not isinstance(exc, SearchFailedError):
        log.error("external search failed unexpectedly", exc_info=exc)
    else:
        log.warning("external search failed: %s", exc)
    if key == "invalid":
        if is_new:   # drop the placeholder and let the user retype the query
            try:
                _bot.delete_message(chat_id, message_id)
            except Exception:  # noqa: BLE001
                pass
            _waiting[user.id] = field
            _bot.send_message(chat_id, _tx("invalid", lang), reply_markup=_cancel_keyboard(user))
        else:
            _bot.send_message(chat_id, _tx("invalid", lang))
        return
    if is_new:       # replace the "searching…" placeholder; offer a fresh search
        _show(chat_id, message_id, _tx(key, lang), _new_search_markup(lang))
    else:            # pagination failure: keep the current results, report separately
        _bot.send_message(chat_id, _tx(key, lang))


# ── formatting ──────────────────────────────────────────────────────────────
def _new_search_markup(lang: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton(_tx("new_search", lang), callback_data=f"{PREFIX}new"))
    return mk


def _page_markup(token: str, page: SearchPage, lang: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    pages = math.ceil(page.total / page.page_size) if page.total else None
    row = []
    if page.page > 1:
        row.append(types.InlineKeyboardButton("◀️", callback_data=f"{PREFIX}p:{token}:{page.page - 1}"))
    row.append(types.InlineKeyboardButton(f"{page.page}/{pages}" if pages else str(page.page),
                                          callback_data=f"{PREFIX}n"))
    if page.has_next:
        row.append(types.InlineKeyboardButton("▶️", callback_data=f"{PREFIX}p:{token}:{page.page + 1}"))
    mk.row(*row)
    mk.row(*[types.InlineKeyboardButton(str(page.start + i + 1),
                                        callback_data=f"{PREFIX}d:{token}:{page.page}:{i}")
             for i in range(len(page.results))])
    mk.add(types.InlineKeyboardButton(_tx("new_search", lang), callback_data=f"{PREFIX}new"))
    return mk


def _has_pdf(r) -> bool:
    return bool(r.pdf_url) and r.access_status != AccessStatus.CLOSED


def _details_markup(token: str, page: int, idx: int, r, lang: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    if _has_pdf(r):
        mk.add(types.InlineKeyboardButton(_tx("btn_pdf", lang), callback_data=f"{PREFIX}f:{token}:{page}:{idx}"))
    mk.add(types.InlineKeyboardButton(_tx("btn_back", lang), callback_data=f"{PREFIX}b:{token}:{page}"))
    mk.add(types.InlineKeyboardButton(_tx("new_search", lang), callback_data=f"{PREFIX}new"))
    return mk


def format_details(r, lang: str) -> str:
    """Details page: title, authors, date, abstract, identifier, source link (HTML, <= SAFE_LEN)."""
    lines = [f"📄 <b>{_e(_short(r.title, 300))}</b>", ""]
    if r.authors:
        names = ", ".join(_e(_short(a, 60)) for a in r.authors[:DETAIL_AUTHORS_MAX])
        if len(r.authors) > DETAIL_AUTHORS_MAX:
            names += " " + _tx("authors_more", lang)
        lines.append(f"{_tx('d_authors', lang)}: {names}")
    if r.published:
        lines.append(f"{_tx('d_date', lang)}: {r.published.isoformat()}")
    if r.identifier:
        lines.append(f"{_tx('d_id', lang)}: <code>{_e(r.identifier)}</code>")
    lines.append(f"{_tx('source', lang)}: {_e(_source_label(r.source))}")
    if r.landing_page_url:
        lines.append(f"{_tx('d_link', lang)}: {_e(r.landing_page_url)}")
    head = "\n".join(lines)
    abstract = _short(r.abstract, ABSTRACT_MAX) if r.abstract else ""
    while True:
        body = _e(abstract) if abstract else _tx("d_no_abstract", lang)
        text = f"{head}\n\n{_tx('d_abstract', lang)}:\n{body}"
        if len(text) <= SAFE_LEN or not abstract:
            break
        abstract = _short(abstract, int(len(abstract) * 0.85))
    return text[:SAFE_LEN]


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _format_item(i: int, r, lang: str, compact: bool) -> str:
    title = _e(_short(r.title, 160))
    head = f'{i}. <a href="{_e(r.landing_page_url)}">{title}</a>' if r.landing_page_url else f"{i}. <b>{title}</b>"
    lines = [head]
    if r.authors and not compact:
        names = ", ".join(_e(_short(a, 40)) for a in r.authors[:3])
        if len(r.authors) > 3:
            names += " " + _tx("authors_more", lang)
        lines.append(f"👤 {names}")
    meta = []
    if r.published:
        meta.append(f"📅 {r.published.isoformat()}")
    if r.identifier:
        meta.append(f"🆔 <code>{_e(r.identifier)}</code>")
    if meta:
        lines.append(" · ".join(meta))
    lines.append(f"{_tx('source', lang)}: {_e(_source_label(r.source))}")
    return "\n".join(lines)


def format_page(page: SearchPage, query: str, field: SearchField, lang: str) -> str:
    field_label = FIELD_LABELS[field][lang]
    q = _e(_short(query, 100))
    if page.total:
        pages = math.ceil(page.total / page.page_size)
        header = _tx("header", lang, field=field_label, query=q, first=page.start + 1,
                     last=page.start + len(page.results), total=page.total, page=page.page, pages=pages)
    else:
        header = _tx("header_unknown_total", lang, field=field_label, query=q, page=page.page)
    for compact in (False, True):
        body = "\n\n".join(_format_item(page.start + i + 1, r, lang, compact)
                           for i, r in enumerate(page.results))
        text = header + "\n\n" + body + "\n\n" + _tx("hint_details", lang)
        if len(text) <= SAFE_LEN:
            return text
    return text[:SAFE_LEN]   # last resort; cannot happen with PAGE_SIZE=5 and capped fields


# ── PDF sending ─────────────────────────────────────────────────────────────
def _start_pdf(c, user, lang: str, token: str, page: int, idx: int, r) -> None:
    chat_id = c.message.chat.id
    if not _has_pdf(r):
        _bot.answer_callback_query(c.id, _tx("pdf_unavailable", lang), show_alert=True)
        return
    key = (user.id, token, page, idx)
    with _ctx_lock:
        now = _clock()
        wait = xs_config.PDF_USER_COOLDOWN - (now - _pdf_last.get(user.id, -1e18))
        if key in _pdf_busy:
            reply = _tx("pdf_busy", lang)
        elif wait > 0:
            reply = _tx("pdf_cooldown", lang, n=math.ceil(wait))
        else:
            reply = None
            _pdf_busy.add(key)
            _pdf_last[user.id] = now        # failed attempts count too: protects the sources
            if len(_pdf_last) > COOLDOWN_MAX_USERS:
                for uid in [u for u, t in _pdf_last.items() if now - t >= xs_config.PDF_USER_COOLDOWN]:
                    del _pdf_last[uid]
    if reply:
        _bot.answer_callback_query(c.id, reply)
        return
    _emit("ext_pdf_requested", user, source=r.source)
    try:
        _bot.answer_callback_query(c.id)
        status = _bot.send_message(chat_id, _tx("pdf_wait", lang))
        _pdf_submit(_pdf_job, chat_id, user, key, r, status.message_id)
    except Exception:
        with _ctx_lock:
            _pdf_busy.discard(key)
        raise


def _pdf_error_key(exc: Exception) -> str:
    if isinstance(exc, PdfUnavailableError):
        return "pdf_unavailable"
    if isinstance(exc, PdfTooLargeError):
        return "pdf_too_large"
    if isinstance(exc, InvalidPdfError):
        return "pdf_invalid"
    if isinstance(exc, ProviderTimeout):
        return "pdf_timeout"
    if isinstance(exc, ProviderNetworkError):
        return "pdf_network"
    return "pdf_generic"


def _pdf_caption(r) -> str:
    cap = f"📄 <b>{_e(_short(r.title, 300))}</b>"
    if r.identifier:
        cap += f"\n🆔 <code>{_e(r.identifier)}</code>"
    if r.landing_page_url:
        cap += f"\n🔗 {_e(r.landing_page_url)}"
    return cap[:1024 - len(SIGNATURE)] + SIGNATURE


def _pdf_job(chat_id: int, user, key, r, status_id: int) -> None:
    """Worker thread: download from the record's own PDF link, validate, send, clean up."""
    lang = _get_lang(user)
    outcome = None                     # None = success, else a TX key for the failure
    try:
        # The temporary directory (and the file in it) is removed on success AND on any error.
        with tempfile.TemporaryDirectory(prefix="xs_pdf_") as tmp:
            try:
                path = asyncio.run(_mgr().download_pdf(r, tmp))
            except Exception as exc:  # noqa: BLE001
                outcome = _pdf_error_key(exc)
                if outcome == "pdf_generic":
                    log.error("external search: PDF download failed unexpectedly", exc_info=exc)
                else:
                    log.warning("external search: PDF download failed: %s", exc)
            else:
                try:
                    with open(path, "rb") as f:
                        _bot.send_document(chat_id, f, caption=_pdf_caption(r), parse_mode="HTML",
                                           visible_file_name=safe_filename(r.identifier))
                except Exception as exc:  # noqa: BLE001
                    outcome = "pdf_send_failed"
                    log.warning("external search: sending PDF failed: %s", exc)
    except Exception:  # noqa: BLE001 - e.g. temp dir creation/cleanup problems
        log.exception("external search: PDF job failed")
        outcome = outcome or "pdf_generic"
    finally:
        with _ctx_lock:
            _pdf_busy.discard(key)
    if outcome is None:
        _emit("ext_pdf_sent", user, source=r.source)
    else:
        _emit("ext_pdf_failed", user, source=r.source, reason=outcome)
    try:
        if outcome is None:
            _bot.delete_message(chat_id, status_id)
        else:
            _show(chat_id, status_id, _tx(outcome, lang), None)
    except Exception:  # noqa: BLE001 - status message is cosmetic
        pass
