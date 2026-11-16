import os
import socket
import unittest
import urllib.error
from datetime import date
from types import SimpleNamespace as NS
from unittest import mock

from telebot import types

import external_search_ui as ui
from external_search import (AccessStatus, BaseProvider, Capability, InvalidQueryError, ProviderNetworkError,
                             ProviderResponseError, ProviderTimeout, SearchField, SearchManager,
                             SearchPage, SearchResult)


class FakeBot:
    def __init__(self):
        self.sent, self.edited, self.answers, self.deleted, self.handlers = [], [], [], [], []
        self.documents, self.doc_error = [], None

    def callback_query_handler(self, func):
        def deco(fn):
            self.handlers.append((func, fn)); return fn
        return deco

    def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw)); return NS(message_id=100 + len(self.sent))

    def edit_message_text(self, text, chat_id, message_id, **kw):
        self.edited.append((chat_id, message_id, text, kw))

    def answer_callback_query(self, cid, text=None, **kw):
        self.answers.append((cid, text, kw))

    def send_document(self, chat_id, document, **kw):
        if self.doc_error:
            raise self.doc_error
        # record what Telegram would upload, and where the temp file lives while it is open
        self.documents.append(dict(chat_id=chat_id, path=document.name, data=document.read(), kw=kw))

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def result(i, **kw):
    d = dict(title=f"Paper <{i}>", source="arxiv", identifier=f"2101.0000{i}v1",
             authors=["A One", "B Two", "C Three", "D Four"], published=date(2021, 1, i),
             landing_page_url=f"https://arxiv.org/abs/2101.0000{i}v1",
             pdf_url=f"https://arxiv.org/pdf/2101.0000{i}v1", access_status=AccessStatus.OPEN,
             abstract="We study <dark> matter & halos.")
    d.update(kw)
    return SearchResult(**d)


class FakeProvider(BaseProvider):
    name = "arxiv"
    capabilities = Capability.SEARCH | Capability.OPEN_ACCESS_PDF
    pdf_hosts = frozenset({"arxiv.org"})

    def __init__(self, total=12, error=None):
        self.total, self.error, self.calls = total, error, []

    def search(self, query, field=SearchField.KEYWORD, page=1, page_size=10):
        self.calls.append((query, field, page, page_size))
        if self.error:
            raise self.error
        start = (page - 1) * page_size
        n = max(0, min(page_size, self.total - start))
        return SearchPage([result(i + 1) for i in range(n)], page, page_size, self.total,
                          start + n < self.total)


USER = NS(id=7, language_code="en")


def setup(provider=None):
    bot = FakeBot()
    prov = provider or FakeProvider()
    mgr = SearchManager(); mgr.register(prov)
    ui.init(bot, lambda u: "en", lambda u: "MAIN_KB", lambda u: "CANCEL_KB",
            is_menu_label=lambda s: s in {"🔍 Search", "🌍 External Search"},
            manager=mgr, submit=lambda fn, *a: fn(*a))
    return bot, prov


def msg(text):
    return NS(from_user=USER, chat=NS(id=1), text=text)


def cb(bot, data, mid=50):
    c = NS(id="cb1", data=data, from_user=USER, message=NS(chat=NS(id=1), message_id=mid))
    func, fn = bot.handlers[0]
    assert func(c)
    fn(c)


def buttons(markup):
    return [b for row in markup.keyboard for b in row]


class FlowTests(unittest.TestCase):
    def test_start_shows_type_picker_with_sources(self):
        bot, _ = setup()
        ui.start(1, USER)
        chat, text, kw = bot.sent[0]
        self.assertIn("arXiv", text)
        self.assertEqual([b.callback_data for b in buttons(kw["reply_markup"])],
                         ["xs:t:title", "xs:t:author", "xs:t:keyword"])

    def test_handler_only_matches_xs_prefix(self):
        bot, _ = setup()
        func = bot.handlers[0][0]
        for data in ("sf:go", "page:p1:2", "search_again", "adm_x", None):
            self.assertFalse(func(NS(data=data)))
        self.assertTrue(func(NS(data="xs:p:1:2")))

    def test_choose_type_then_query_runs_search(self):
        bot, prov = setup()
        cb(bot, "xs:t:author")
        self.assertTrue(ui.is_waiting(7))
        self.assertIn("author name", bot.sent[-1][1])
        self.assertEqual(bot.sent[-1][2]["reply_markup"], "CANCEL_KB")
        self.assertTrue(ui.handle_text(msg("Albert Einstein")))
        self.assertFalse(ui.is_waiting(7))
        self.assertEqual(prov.calls, [("Albert Einstein", SearchField.AUTHOR, 1, ui.PAGE_SIZE)])
        self.assertEqual(bot.sent[-1][2]["reply_markup"], "MAIN_KB")   # placeholder restores menu
        chat, mid, text, kw = bot.edited[-1]
        self.assertEqual(mid, 100 + len(bot.sent))     # results edit the placeholder message
        self.assertIn("Author", text)
        self.assertIn("arXiv", text)

    def test_not_waiting_not_consumed(self):
        setup()
        self.assertFalse(ui.handle_text(msg("hello")))

    def test_cancel_and_menu_label(self):
        bot, _ = setup()
        cb(bot, "xs:t:title")
        self.assertTrue(ui.handle_text(msg("❌ Cancel")))
        self.assertFalse(ui.is_waiting(7))
        self.assertEqual(bot.sent[-1][1], "↩️ Cancelled.")
        cb(bot, "xs:t:title")
        self.assertFalse(ui.handle_text(msg("🔍 Search")))   # falls through to bot.py
        self.assertFalse(ui.is_waiting(7))

    def test_invalid_and_too_long_keep_waiting(self):
        bot, prov = setup()
        cb(bot, "xs:t:keyword")
        self.assertTrue(ui.handle_text(msg("!!! ???")))
        self.assertTrue(ui.is_waiting(7))
        self.assertIn("Invalid", bot.sent[-1][1])
        self.assertTrue(ui.handle_text(msg("x" * (ui.MAX_QUERY_LEN + 1))))
        self.assertIn("too long", bot.sent[-1][1])
        self.assertTrue(ui.is_waiting(7))
        self.assertEqual(prov.calls, [])

    def test_provider_invalid_query_rearms(self):
        bot, _ = setup(FakeProvider(error=InvalidQueryError("empty")))
        cb(bot, "xs:t:title")
        ui.handle_text(msg("ok text"))
        self.assertTrue(ui.is_waiting(7))
        self.assertEqual(len(bot.deleted), 1)
        self.assertIn("Invalid", bot.sent[-1][1])


class PaginationTests(unittest.TestCase):
    def search(self, total=12):
        bot, prov = setup(FakeProvider(total=total))
        cb(bot, "xs:t:title")
        ui.handle_text(msg("dark matter"))
        return bot, prov

    def test_first_page_buttons(self):
        bot, _ = self.search()
        mk = bot.edited[-1][3]["reply_markup"]
        data = [b.callback_data for b in buttons(mk)]
        self.assertEqual(len(data), 2 + 5 + 1)          # pager (n/N, ▶️), 5 result numbers, new search
        self.assertEqual((data[0], data[-1]), ("xs:n", "xs:new"))   # page indicator is a no-op
        row = mk.keyboard[0]
        self.assertEqual([b.text for b in row], ["1/3", "▶️"])
        self.assertTrue(row[1].callback_data.startswith("xs:p:") and row[1].callback_data.endswith(":2"))
        self.assertLessEqual(max(len(b.callback_data.encode()) for b in buttons(mk)), 64)

    def test_next_prev_preserve_query_and_field(self):
        bot, prov = self.search()
        token = bot.edited[-1][3]["reply_markup"].keyboard[0][1].callback_data.split(":")[2]
        cb(bot, f"xs:p:{token}:2", mid=77)
        self.assertEqual(prov.calls[-1], ("dark matter", SearchField.TITLE, 2, ui.PAGE_SIZE))
        chat, mid, text, kw = bot.edited[-1]
        self.assertEqual(mid, 77)
        self.assertIn("6–10 of 12", text)
        self.assertIn("page 2/3", text)
        self.assertEqual([b.text for b in kw["reply_markup"].keyboard[0]], ["◀️", "2/3", "▶️"])
        cb(bot, f"xs:p:{token}:3", mid=77)
        self.assertEqual([b.text for b in bot.edited[-1][3]["reply_markup"].keyboard[0]], ["◀️", "3/3"])
        cb(bot, f"xs:p:{token}:2", mid=77)
        self.assertEqual(prov.calls[-1][2], 2)

    def test_expired_and_bad_callbacks(self):
        bot, prov = setup()
        cb(bot, "xs:p:zz:2")
        self.assertIn("expired", bot.answers[-1][1])
        n = len(prov.calls)
        for bad in ("xs:p:1:abc", "xs:t:nope", "xs:zzz", "xs:p:1"):
            cb(bot, bad)
        self.assertEqual(len(prov.calls), n)

    def test_page_beyond_results_keeps_current_page(self):
        bot, _ = self.search(total=3)
        only = bot.edited[-1][3]["reply_markup"].keyboard[0][0]  # single page: just the "1/1" no-op button
        self.assertEqual(only.callback_data, "xs:n")
        tok = next(iter(ui._ctx))
        edits = len(bot.edited)
        cb(bot, f"xs:p:{tok}:9")
        self.assertEqual(len(bot.edited), edits)
        self.assertIn("No more", bot.sent[-1][1])

    def test_new_search_button(self):
        bot, _ = setup()
        cb(bot, "xs:new")
        self.assertIn("Choose the search type", bot.sent[-1][1])


class FormattingTests(unittest.TestCase):
    def test_escaping_and_fields(self):
        page = SearchPage([result(1)], 1, 5, 1, False)
        text = ui.format_page(page, 'a "<b>"', SearchField.KEYWORD, "en")
        self.assertIn("Paper &lt;1&gt;", text)
        self.assertIn("&lt;b&gt;", text)
        self.assertIn("A One, B Two, C Three et al.", text)
        self.assertIn("2021-01-01", text)
        self.assertIn("<code>2101.00001v1</code>", text)
        self.assertIn("Source: arXiv", text)

    def test_persian_labels(self):
        page = SearchPage([result(1)], 1, 5, 1, False)
        text = ui.format_page(page, "q", SearchField.TITLE, "fa")
        self.assertIn("منبع: arXiv", text)
        self.assertIn("عنوان", text)

    def test_length_guard(self):
        long = [result(i, title="T" * 500, authors=["N" * 200] * 10) for i in range(1, 6)]
        page = SearchPage(long, 1, 5, 99, True)
        self.assertLessEqual(len(ui.format_page(page, "q" * 200, SearchField.TITLE, "en")), ui.SAFE_LEN)

    def test_missing_optional_fields(self):
        r = SearchResult(title="Only title", source="arxiv")
        text = ui.format_page(SearchPage([r], 1, 5, None, False), "q", SearchField.TITLE, "en")
        self.assertIn("<b>Only title</b>", text)
        self.assertIn("Page 1", text)


class ErrorTests(unittest.TestCase):
    def run_err(self, err):
        bot, _ = setup(FakeProvider(error=err))
        cb(bot, "xs:t:title")
        ui.handle_text(msg("quantum"))
        return bot.edited[-1][2], bot.edited[-1][3]["reply_markup"]

    def test_messages(self):
        for err, frag in ((ProviderTimeout("t"), "in time"), (ProviderNetworkError("n"), "Could not reach"),
                          (ProviderResponseError("r"), "invalid response"), (RuntimeError("x"), "went wrong")):
            text, mk = self.run_err(err)
            self.assertIn(frag, text)
            self.assertEqual(buttons(mk)[0].callback_data, "xs:new")

    def test_no_results(self):
        bot, _ = setup(FakeProvider(total=0))
        cb(bot, "xs:t:title"); ui.handle_text(msg("zzzz"))
        self.assertIn("No results", bot.edited[-1][2])

    def test_pagination_error_keeps_results(self):
        bot, prov = setup()
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        prov.error = ProviderTimeout("t")
        edits = len(bot.edited)
        cb(bot, f"xs:p:{tok}:2")
        self.assertEqual(len(bot.edited), edits)
        self.assertIn("in time", bot.sent[-1][1])

    def test_no_provider_available(self):
        bot = FakeBot()
        ui.init(bot, lambda u: "en", lambda u: "MAIN_KB", lambda u: "C", manager=SearchManager(),
                submit=lambda fn, *a: fn(*a))
        ui.start(1, USER)
        self.assertIn("unavailable", bot.sent[-1][1])

    def test_context_store_bounded(self):
        setup()
        for i in range(ui.CTX_MAX + 20):
            ui._ctx_put(f"q{i}", SearchField.TITLE)
        self.assertEqual(len(ui._ctx), ui.CTX_MAX)


class IntegrationWithRealProviderTests(unittest.TestCase):
    def test_arxiv_provider_with_mocked_http(self):
        from external_search.providers.arxiv import ArxivProvider
        xml = (b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
               b'<opensearch:totalResults>1</opensearch:totalResults><entry><id>http://arxiv.org/abs/2101.00001v1</id>'
               b'<published>2021-01-01T00:00:00Z</published><title>Real</title><author><name>X Y</name></author></entry></feed>')
        urls = []
        bot = FakeBot()
        mgr = SearchManager(); mgr.register(ArxivProvider(fetch=lambda u: urls.append(u) or xml, min_interval=0))
        ui.init(bot, lambda u: "en", lambda u: "M", lambda u: "C", manager=mgr, submit=lambda fn, *a: fn(*a))
        cb(bot, "xs:t:title"); ui.handle_text(msg("a&b"))
        self.assertIn("ti%3A%22a%26b%22", urls[0])
        self.assertIn("Source: arXiv", bot.edited[-1][2])


PDF = b"%PDF-1.4\n" + b"z" * 300 + b"\n%%EOF\n"


class FakeResp:
    def __init__(self, body=PDF, ctype="application/pdf", url=None):
        self._b, self._pos, self._url = body, 0, url
        self.status, self.headers = 200, {"Content-Type": ctype, "Content-Length": str(len(body))}

    def read(self, n=-1):
        out = self._b[self._pos:self._pos + n]; self._pos += len(out); return out

    def geturl(self):
        return self._url

    def __enter__(self): return self
    def __exit__(self, *a): return False


class DetailsTests(unittest.TestCase):
    def setUp(self):
        self.bot, self.prov = setup(FakeProvider(total=12))
        cb(self.bot, "xs:t:title"); ui.handle_text(msg("dark matter"))
        self.tok = next(iter(ui._ctx))

    def test_results_have_number_buttons(self):
        mk = self.bot.edited[-1][3]["reply_markup"]
        nums = mk.keyboard[1]
        self.assertEqual([b.text for b in nums], ["1", "2", "3", "4", "5"])
        self.assertEqual(nums[2].callback_data, f"xs:d:{self.tok}:1:2")
        self.assertLessEqual(max(len(b.callback_data.encode()) for b in buttons(mk)), 64)
        self.assertIn("Tap a result number", self.bot.edited[-1][2])

    def test_page_two_numbers_are_global_but_index_local(self):
        cb(self.bot, f"xs:p:{self.tok}:2")
        nums = self.bot.edited[-1][3]["reply_markup"].keyboard[1]
        self.assertEqual([b.text for b in nums], ["6", "7", "8", "9", "10"])
        self.assertEqual(nums[0].callback_data, f"xs:d:{self.tok}:2:0")

    def test_details_page_content(self):
        n = len(self.prov.calls)
        cb(self.bot, f"xs:d:{self.tok}:1:1", mid=77)
        chat, mid, text, kw = self.bot.edited[-1]
        self.assertEqual(mid, 77)
        for frag in ("Paper &lt;2&gt;", "A One, B Two, C Three, D Four", "2021-01-02",
                     "<code>2101.00002v1</code>", "https://arxiv.org/abs/2101.00002v1",
                     "We study &lt;dark&gt; matter &amp; halos.", "Abstract", "Source: arXiv"):
            self.assertIn(frag, text)
        self.assertEqual([b.callback_data for b in buttons(kw["reply_markup"])],
                         [f"xs:f:{self.tok}:1:1", f"xs:b:{self.tok}:1", "xs:new"])
        self.assertTrue(kw["disable_web_page_preview"])
        self.assertEqual(len(self.prov.calls), n)        # no new search

    def test_details_persian(self):
        ui.init(self.bot, lambda u: "fa", lambda u: "M", lambda u: "C",
                manager=ui._mgr(), submit=lambda fn, *a: fn(*a))
        cb(self.bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        cb(self.bot, f"xs:d:{tok}:1:0")
        text = self.bot.edited[-1][2]
        for frag in ("نویسندگان", "تاریخ", "چکیده", "شناسه", "لینک منبع"):
            self.assertIn(frag, text)
        self.assertEqual(buttons(self.bot.edited[-1][3]["reply_markup"])[0].text, "📄 دریافت PDF")

    def test_back_restores_list_without_search(self):
        n = len(self.prov.calls)
        cb(self.bot, f"xs:d:{self.tok}:1:0", mid=77)
        cb(self.bot, f"xs:b:{self.tok}:1", mid=77)
        chat, mid, text, kw = self.bot.edited[-1]
        self.assertEqual(mid, 77)
        self.assertIn("1–5 of 12", text)
        self.assertEqual(kw["reply_markup"].keyboard[1][0].callback_data, f"xs:d:{self.tok}:1:0")
        self.assertEqual(len(self.prov.calls), n)

    def test_long_and_missing_fields(self):
        r = SearchResult(title="T" * 900, source="arxiv", authors=["N" * 300] * 40,
                         abstract="&<>" * 5000, published=date(2020, 5, 6))
        text = ui.format_details(r, "en")
        self.assertLessEqual(len(text), ui.SAFE_LEN)
        self.assertIn("et al.", text)
        bare = ui.format_details(SearchResult(title="Only", source="arxiv"), "en")
        self.assertIn("<b>Only</b>", bare)
        self.assertIn("No abstract available.", bare)
        self.assertNotIn("Authors", bare)

    def test_no_pdf_button_without_pdf_link_or_when_closed(self):
        mk = ui._details_markup("t", 1, 0, SearchResult(title="x", source="arxiv"), "en")
        self.assertNotIn("f:", "".join(b.callback_data for b in buttons(mk)))
        mk = ui._details_markup("t", 1, 0, SearchResult(title="x", source="arxiv", pdf_url="https://arxiv.org/pdf/1",
                                                         access_status=AccessStatus.CLOSED), "en")
        self.assertNotIn("xs:f:", "".join(b.callback_data for b in buttons(mk)))

    def test_expired_and_bad_detail_callbacks(self):
        n = len(self.bot.edited)
        cb(self.bot, "xs:d:zz:1:0"); self.assertIn("expired", self.bot.answers[-1][1])
        cb(self.bot, "xs:b:zz:1"); self.assertIn("expired", self.bot.answers[-1][1])
        cb(self.bot, "xs:f:zz:1:0"); self.assertIn("expired", self.bot.answers[-1][1])
        for bad in (f"xs:d:{self.tok}:1:9", f"xs:d:{self.tok}:1:-1", f"xs:d:{self.tok}:x:0",
                    f"xs:f:{self.tok}:1:abc", f"xs:b:{self.tok}:q", f"xs:d:{self.tok}:1"):
            cb(self.bot, bad)
        self.assertEqual(len(self.bot.edited), n)
        self.assertEqual(self.bot.documents, [])

    def test_page_cache_bounded(self):
        for i in range(ui.PAGES_MAX + 10):
            ui._pages_put(f"t{i}", SearchPage([result(1)], 1, 5, 1, False), "q", SearchField.TITLE)
        self.assertEqual(len(ui._pages), ui.PAGES_MAX)


class MultiSourceTests(unittest.TestCase):
    def test_three_sources_one_page_fits_one_button_row(self):
        def make(nm):
            class P(FakeProvider):
                name = nm

                def search(self, query, field=SearchField.KEYWORD, page=1, page_size=10):
                    n = min(page_size, 40)
                    return SearchPage([result(i + 1, title=f"{nm} distinct paper number {i}", source=nm,
                                              identifier=f"{nm}.{i}", pdf_url=None,
                                              landing_page_url=f"https://{nm}.example/{i}") for i in range(n)],
                                      page, page_size, 40, True)
            return P()
        bot = FakeBot(); mgr = SearchManager()
        for p in (make('arxiv'), make('zenodo'), make('hal')):
            mgr.register(p)
        ui.init(bot, lambda u: "en", lambda u: "M", lambda u: "C", manager=mgr, submit=lambda fn, *a: fn(*a))
        ui.start(1, USER)
        self.assertIn("arXiv, Zenodo, HAL", bot.sent[-1][1])
        cb(bot, "xs:t:title"); ui.handle_text(msg("some distinct query words"))
        text, mk = bot.edited[-1][2], bot.edited[-1][3]["reply_markup"]
        nums = mk.keyboard[1]
        self.assertLessEqual(len(nums), 8)                      # Telegram: at most 8 buttons per row
        self.assertEqual(len(nums), 6)                          # ceil(5/3) per source x 3
        self.assertEqual([b.text for b in nums], ["1", "2", "3", "4", "5", "6"])
        for name in ("arXiv", "Zenodo", "HAL"):
            self.assertIn(f"Source: {name}", text)
        self.assertEqual(ui._pages_get(next(iter(ui._ctx)), 1)[0].page_size, 6)
        self.assertIn("of 120", text)                           # totals add up across sources


class DateTextTests(unittest.TestCase):
    def test_year_fallback_for_sources_without_exact_dates(self):
        r = SearchResult(title="T", source="ads", identifier="2021ApJ...910L...1E", publication_year=2021)
        self.assertIn("📅 2021", ui._format_item(1, r, "en", True))
        self.assertIn("Date</b>: 2021", ui.format_details(r, "en"))
        self.assertIn("NASA ADS", ui._format_item(1, r, "en", True))
        r.published = date(2021, 3, 4)
        self.assertIn("2021-03-04", ui.format_details(r, "en"))
        self.assertNotIn("Date", ui.format_details(SearchResult(title="T", source="ads"), "en"))


class BilingualTests(unittest.TestCase):
    def test_all_texts_have_both_languages_with_same_placeholders(self):
        import re
        ph = lambda t: sorted(re.findall(r"{(\w+)}", t))
        for key, d in ui.TX.items():
            self.assertEqual(set(d), {"fa", "en"}, key)
            self.assertEqual(ph(d["fa"]), ph(d["en"]), key)
            self.assertTrue(d["fa"].strip() and d["en"].strip(), key)
        for table in (ui.FIELD_LABELS.values(), [ui.BUTTON_LABEL]):
            for d in table:
                self.assertEqual(set(d), {"fa", "en"})

    def test_no_telescope_emoji_anywhere(self):
        import inspect
        self.assertNotIn("\U0001F52D", inspect.getsource(ui))
        self.assertIn("🌍", ui.BUTTON_LABEL["fa"]); self.assertIn("🌍", ui.BUTTON_LABEL["en"])
        self.assertNotEqual(ui.BUTTON_LABEL["fa"], ui.BUTTON_LABEL["en"])

    def test_language_follows_get_lang_at_every_step(self):
        bot = FakeBot(); lang = ["en"]
        mgr = SearchManager(); mgr.register(FakeProvider())
        ui.init(bot, lambda u: lang[0], lambda u: "M", lambda u: "C", manager=mgr,
                submit=lambda fn, *a: fn(*a))
        ui.start(1, USER)
        self.assertIn("Choose the search type", bot.sent[-1][1])
        self.assertEqual(buttons(bot.sent[-1][2]["reply_markup"])[0].text, "📖 Title")
        lang[0] = "fa"                                  # user switches language in the bot menu
        ui.start(1, USER)
        self.assertIn("نوع جست‌وجو", bot.sent[-1][1])
        self.assertEqual(buttons(bot.sent[-1][2]["reply_markup"])[0].text, "📖 عنوان")
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        self.assertIn("نتیجه", bot.edited[-1][2])
        self.assertEqual(buttons(bot.edited[-1][3]["reply_markup"])[-1].text, "🌍 جست‌وجوی جدید")
        cb(bot, f"xs:d:{tok}:1:0")
        self.assertIn("چکیده", bot.edited[-1][2])
        self.assertEqual([b.text for b in buttons(bot.edited[-1][3]["reply_markup"])][:2],
                         ["📄 دریافت PDF", "◀️ بازگشت به نتایج"])
        lang[0] = "en"
        cb(bot, f"xs:b:{tok}:1")
        self.assertIn("results", bot.edited[-1][2])
        self.assertEqual(buttons(bot.edited[-1][3]["reply_markup"])[-1].text, "🌍 New search")

    def test_switching_language_button_leaves_waiting_state(self):
        bot, _ = setup()
        ui.init(bot, lambda u: "en", lambda u: "M", lambda u: "C",
                is_menu_label=lambda s: s in {"🌐 فارسی", "🌐 English"}, manager=ui._mgr(),
                submit=lambda fn, *a: fn(*a))
        cb(bot, "xs:t:title")
        self.assertFalse(ui.handle_text(msg("🌐 فارسی")))    # not consumed: bot.py switches language
        self.assertFalse(ui.is_waiting(7))


class EventTests(unittest.TestCase):
    def setup_events(self, provider=None, boom=False):
        self.events = []

        def hook(ev, uid, extra=None):
            if boom:
                raise RuntimeError("analytics down")
            self.events.append((ev, uid, extra))
        bot = FakeBot()
        mgr = SearchManager(); mgr.register(provider or FakeProvider(total=12))
        ui.init(bot, lambda u: "en", lambda u: "M", lambda u: "C", manager=mgr,
                submit=lambda fn, *a: fn(*a), on_event=hook)
        return bot

    def names(self):
        return [e[0] for e in self.events]

    def test_search_events_and_pagination_not_counted(self):
        bot = self.setup_events()
        cb(bot, "xs:t:author"); ui.handle_text(msg("secret query"))
        self.assertEqual(self.names(), ["ext_search_submitted", "ext_search_completed"])
        self.assertEqual(self.events[0], ("ext_search_submitted", 7, {"field": "author"}))
        self.assertEqual(self.events[1][2], {"field": "author", "result_count": 12})
        tok = next(iter(ui._ctx))
        cb(bot, f"xs:p:{tok}:2")
        self.assertEqual(len(self.events), 2)                      # paging is not a new search
        self.assertNotIn("secret", repr(self.events))               # query text never reported

    def test_no_results_and_errors(self):
        bot = self.setup_events(FakeProvider(total=0))
        cb(bot, "xs:t:title"); ui.handle_text(msg("zzz"))
        self.assertEqual(self.names(), ["ext_search_submitted", "ext_search_no_results"])
        bot = self.setup_events(FakeProvider(error=ProviderTimeout("t")))
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        self.assertEqual(self.events[-1], ("ext_search_error", 7, {"field": "title", "kind": "err_timeout", "paged": False}))
        bot = self.setup_events(FakeProvider(error=InvalidQueryError("bad")))
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        self.assertNotIn("ext_search_error", self.names())          # bad input is not a technical error

    def test_details_and_pdf_events(self):
        bot = self.setup_events()
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        cb(bot, f"xs:d:{tok}:1:0")
        self.assertEqual(self.events[-1], ("ext_details_viewed", 7, {"source": "arxiv"}))
        opener = lambda url, timeout: FakeResp(url=url)
        with mock.patch("external_search.utils.pdf._default_opener", return_value=opener):
            cb(bot, f"xs:f:{tok}:1:0")
        self.assertEqual(self.names()[-2:], ["ext_pdf_requested", "ext_pdf_sent"])
        ui._pdf_last.clear()
        bad = lambda url, timeout: FakeResp(b"<html></html>", url=url)
        with mock.patch("external_search.utils.pdf._default_opener", return_value=bad):
            cb(bot, f"xs:f:{tok}:1:1")
        self.assertEqual(self.events[-1], ("ext_pdf_failed", 7, {"source": "arxiv", "reason": "pdf_invalid"}))

    def test_cooldown_rejection_not_counted_and_hook_failure_is_harmless(self):
        bot = self.setup_events()
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        opener = lambda url, timeout: FakeResp(url=url)
        with mock.patch("external_search.utils.pdf._default_opener", return_value=opener):
            cb(bot, f"xs:f:{tok}:1:0"); cb(bot, f"xs:f:{tok}:1:1")   # second hits the cooldown
        self.assertEqual(self.names().count("ext_pdf_requested"), 1)
        bot = self.setup_events(boom=True)                           # analytics failure must not break the flow
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        self.assertIn("results", bot.edited[-1][2])

    def test_without_hook_nothing_happens(self):
        bot, _ = setup()
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        self.assertIn("results", bot.edited[-1][2])


class MultiUserTests(unittest.TestCase):
    def test_tokens_are_unique_and_carry_process_prefix(self):
        setup()
        a, b = ui._ctx_put("q", SearchField.TITLE), ui._ctx_put("q", SearchField.TITLE)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(ui._BOOT))
        self.assertNotEqual(a, "1")      # a bare counter would collide with pre-restart buttons

    def test_waiting_state_and_pages_are_per_user(self):
        bot, prov = setup()
        other = NS(id=8, language_code="en")
        cb(bot, "xs:t:title")
        self.assertTrue(ui.is_waiting(7)); self.assertFalse(ui.is_waiting(8))
        self.assertFalse(ui.handle_text(NS(from_user=other, chat=NS(id=2), text="hello")))
        self.assertTrue(ui.is_waiting(7))
        ui.handle_text(msg("alpha"))
        ui.clear(8)
        self.assertEqual(prov.calls[-1][0], "alpha")

    def test_pdf_busy_is_per_user_and_page_item(self):
        bot, _ = setup()
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        ui._pdf_busy.add((8, tok, 1, 0))                 # another user is fetching the same item
        def opener(url, timeout): return FakeResp(url=url)
        with mock.patch("external_search.utils.pdf._default_opener", return_value=opener):
            cb(bot, f"xs:f:{tok}:1:0")
        self.assertEqual(len(bot.documents), 1)
        self.assertEqual(ui._pdf_busy, {(8, tok, 1, 0)})  # only our own key was released

    def test_pdf_jobs_use_a_separate_submit(self):
        bot = FakeBot(); jobs = []
        mgr = SearchManager(); mgr.register(FakeProvider())
        ui.init(bot, lambda u: "en", lambda u: "M", lambda u: "C", manager=mgr,
                submit=lambda fn, *a: fn(*a), pdf_submit=lambda fn, *a: jobs.append(fn.__name__))
        cb(bot, "xs:t:title"); ui.handle_text(msg("q"))
        tok = next(iter(ui._ctx))
        cb(bot, f"xs:f:{tok}:1:0")
        self.assertEqual(jobs, ["_pdf_job"])


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.bot, self.prov = setup(FakeProvider(total=12))
        cb(self.bot, "xs:t:title"); ui.handle_text(msg("dark matter"))
        self.tok = next(iter(ui._ctx))
        self.urls = []
        self.now = [1000.0]
        ui._clock = lambda: self.now[0]
        self.addCleanup(setattr, ui, "_clock", __import__("time").monotonic)

    def click(self, resp=None, exc=None, idx=0, advance=True):
        if advance:
            self.now[0] += 1000          # past any cooldown
        def opener(url, timeout):
            self.urls.append((url, timeout))
            if exc:
                raise exc
            r = resp or FakeResp()
            r._url = r._url or url
            return r
        with mock.patch("external_search.utils.pdf._default_opener", return_value=opener):
            cb(self.bot, f"xs:f:{self.tok}:1:{idx}", mid=60)

    def assertCleaned(self):
        for d in self.bot.documents:
            self.assertFalse(os.path.exists(d["path"]))
            self.assertFalse(os.path.exists(os.path.dirname(d["path"])))
        self.assertEqual(ui._pdf_busy, set())

    def last_status(self):
        return self.bot.edited[-1][2]

    def test_success_sends_pdf_from_record_link_and_cleans_up(self):
        self.click(idx=1)
        self.assertEqual([u for u, _ in self.urls], ["https://arxiv.org/pdf/2101.00002v1"])
        self.assertTrue(self.urls[0][1] > 0)
        (doc,) = self.bot.documents
        self.assertEqual(doc["chat_id"], 1)
        self.assertEqual(doc["data"], PDF)
        self.assertEqual(doc["kw"]["visible_file_name"], "2101.00002v1.pdf")
        self.assertLessEqual(len(doc["kw"]["caption"]), 1024)
        self.assertIn("Paper &lt;2&gt;", doc["kw"]["caption"])
        self.assertTrue(doc["kw"]["caption"].endswith("\n\n@PhysisLib_Bot"))
        long = ui._pdf_caption(SearchResult(title="T" * 5000, source="arxiv", identifier="1" * 900,
                                            landing_page_url="https://arxiv.org/abs/" + "9" * 900))
        self.assertLessEqual(len(long), 1024)
        self.assertTrue(long.endswith("@PhysisLib_Bot"))
        self.assertEqual(doc["kw"]["parse_mode"], "HTML")
        self.assertIn("Fetching", self.bot.sent[-1][1])
        self.assertEqual(self.bot.deleted[-1][0], 1)     # status message removed
        self.assertCleaned()

    def test_html_response_is_not_sent(self):
        for resp in (FakeResp(b"<html>blocked</html>", ctype="text/html"),
                     FakeResp(b"<html>blocked</html>"), FakeResp(b"")):
            self.bot.documents.clear()
            self.click(resp)
            self.assertEqual(self.bot.documents, [])
            self.assertIn("not a valid PDF", self.last_status())
        self.assertCleaned()

    def test_error_messages(self):
        def http(code):
            return urllib.error.HTTPError("https://arxiv.org/pdf/x", code, "x", {}, None)
        cases = ((http(404), "not available at its source"), (http(503), "Could not reach the PDF source"),
                 (socket.timeout("t"), "timed out"), (urllib.error.URLError(OSError("x")), "Could not reach the PDF source"),
                 (RuntimeError("boom"), "went wrong"))
        for exc, frag in cases:
            self.click(exc=exc)
            self.assertIn(frag, self.last_status(), exc)
            self.assertEqual(self.bot.documents, [])
            self.assertEqual(ui._pdf_busy, set())

    def test_too_large(self):
        big = FakeResp()
        big.headers["Content-Length"] = str(10 ** 10)
        self.click(big)
        self.assertIn("larger than the allowed size", self.last_status())
        self.assertEqual(self.bot.documents, [])

    def test_send_failure_reports_and_cleans_up(self):
        self.bot.doc_error = RuntimeError("telegram said no")
        seen = []
        orig = self.bot.send_document

        def spy(chat_id, document, **kw):
            seen.append(document.name)
            return orig(chat_id, document, **kw)
        self.bot.send_document = spy
        self.click()
        self.assertIn("sending it on Telegram failed", self.last_status())
        self.assertEqual(len(seen), 1)
        self.assertFalse(os.path.exists(seen[0]))
        self.assertFalse(os.path.exists(os.path.dirname(seen[0])))
        self.assertEqual(ui._pdf_busy, set())

    def test_non_arxiv_link_is_never_requested(self):
        page = ui._pages_get(self.tok, 1)[0]
        page.results[0].pdf_url = "https://example.org/other.pdf"
        self.click()
        self.assertEqual(self.urls, [])
        self.assertIn("not available at its source", self.last_status())
        self.assertEqual(self.bot.documents, [])

    def test_record_without_pdf_link(self):
        ui._pages_get(self.tok, 1)[0].results[0].pdf_url = None
        self.click()
        self.assertEqual(self.urls, [])
        self.assertIn("not available", self.bot.answers[-1][1])

    def test_cooldown_per_user(self):
        cd = ui.xs_config.PDF_USER_COOLDOWN
        self.click(advance=False)
        self.assertEqual(len(self.bot.documents), 1)
        self.now[0] += cd - 3                       # too soon, even for a different item
        self.click(idx=1, advance=False)
        self.assertEqual(len(self.bot.documents), 1)
        self.assertEqual(len(self.urls), 1)
        self.assertIn("wait 3 s", self.bot.answers[-1][1])
        self.assertEqual(ui._pdf_busy, set())
        ui._pdf_last[8] = self.now[0]               # another user's cooldown is independent
        self.now[0] += 3
        self.click(idx=1, advance=False)            # cooldown over for user 7
        self.assertEqual(len(self.bot.documents), 2)

    def test_failed_attempt_still_starts_cooldown(self):
        self.click(exc=RuntimeError("boom"), advance=False)
        self.click(advance=False)
        self.assertEqual(self.bot.documents, [])
        self.assertIn("Please wait", self.bot.answers[-1][1])

    def test_cooldown_store_bounded(self):
        for i in range(ui.COOLDOWN_MAX_USERS + 50):
            ui._pdf_last[10_000 + i] = -1e9
        self.click()
        self.assertLess(len(ui._pdf_last), ui.COOLDOWN_MAX_USERS)

    def test_double_click_does_not_start_second_download(self):
        ui._pdf_busy.add((7, self.tok, 1, 0))
        self.click()
        self.assertEqual(self.urls, [])
        self.assertIn("already being fetched", self.bot.answers[-1][1])
        ui._pdf_busy.clear()
        self.click()
        self.assertEqual(len(self.bot.documents), 1)

    def test_temp_dir_removed_even_if_download_crashes_midway(self):
        made = []
        real = ui.tempfile.TemporaryDirectory

        def spy(*a, **k):
            d = real(*a, **k); made.append(d.name); return d
        with mock.patch.object(ui.tempfile, "TemporaryDirectory", spy):
            self.click(FakeResp(b"<html>" + b"a" * 5000))
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))


if __name__ == "__main__":
    unittest.main()
