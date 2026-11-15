"""Tests for the external-search analytics queries and dashboard section.

Runs against an in-memory SQLite and a stub `database` module (injected only while this
module's tests run), so it needs neither the real database nor Telegram.
"""
import importlib
import sqlite3
import sys
import types as pytypes
import unittest
from unittest import mock

_patcher = None
analytics = dash = conn = None
LANG = {}


def setUpModule():
    global _patcher, analytics, dash, conn
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, created_at TEXT, last_seen TEXT);
        CREATE TABLE analytics_events (id INTEGER PRIMARY KEY, event_type TEXT, user_id INTEGER,
                                       resource_id INTEGER, extra TEXT, created_at TEXT);
    """)
    db = pytypes.ModuleType("database")
    db.get_connection = lambda: conn
    db.get_user_lang = lambda uid: LANG.get(uid, "en")
    _patcher = mock.patch.dict(sys.modules, {"database": db})
    _patcher.start()
    for name in ("analytics", "analytics_dashboard"):
        sys.modules.pop(name, None)
    analytics = importlib.import_module("analytics")
    dash = importlib.import_module("analytics_dashboard")


def tearDownModule():
    _patcher.stop()                      # restores sys.modules (stub database and modules removed)
    conn.close()


def add(event, uid, ago="0 days"):
    conn.execute("INSERT INTO analytics_events (event_type, user_id, created_at) "
                 "VALUES (?, ?, datetime('now', ?))", (event, uid, f"-{ago}"))


class Base(unittest.TestCase):
    def setUp(self):
        conn.execute("DELETE FROM analytics_events"); conn.execute("DELETE FROM users")
        LANG.clear()


class ExternalStatsTests(Base):
    def seed(self):
        for _ in range(4): add("ext_search_submitted", 1)
        add("ext_search_submitted", 2)
        for ev, n in (("ext_search_completed", 3), ("ext_search_no_results", 1), ("ext_search_error", 1),
                      ("ext_details_viewed", 4), ("ext_pdf_requested", 3), ("ext_pdf_sent", 2),
                      ("ext_pdf_failed", 1)):
            for _ in range(n): add(ev, 1)
        add("ext_search_submitted", 3, ago="40 days")      # old event: only in all-time / 90d

    def test_counts_and_rates(self):
        self.seed()
        s = analytics.get_external_search_stats_in_period()
        self.assertEqual((s["total_submitted"], s["total_completed"], s["total_no_results"], s["total_errors"]),
                         (6, 3, 1, 1))
        self.assertEqual((s["details_viewed"], s["pdf_requested"], s["pdf_sent"], s["pdf_failed"]), (4, 3, 2, 1))
        self.assertEqual(s["unique_searchers"], 3)
        self.assertEqual(s["search_success_rate_pct"], 50.0)
        self.assertEqual(s["pdf_success_rate_pct"], 66.7)

    def test_period_filter(self):
        self.seed()
        s30 = analytics.get_external_search_stats_in_period(days=30)
        self.assertEqual((s30["total_submitted"], s30["unique_searchers"]), (5, 2))
        self.assertEqual(analytics.get_external_search_stats_in_period(days=90)["total_submitted"], 6)
        yest = analytics.get_external_search_stats_in_period(days=1, offset_days=1)
        self.assertEqual(yest["total_submitted"], 0)

    def test_empty_has_zero_rates(self):
        s = analytics.get_external_search_stats_in_period(days=7)
        self.assertEqual((s["total_submitted"], s["search_success_rate_pct"], s["pdf_success_rate_pct"]), (0, 0.0, 0.0))

    def test_local_stats_are_not_polluted_and_vice_versa(self):
        self.seed()
        add("search_submitted", 9); add("file_requested", 9); add("file_sent", 9)
        local = analytics.get_search_stats()
        self.assertEqual(local["total_submitted"], 1)                       # ext_* not counted as local
        self.assertEqual(analytics.get_delivery_stats()["total_requested"], 1)
        self.assertEqual(analytics.get_external_search_stats_in_period()["total_submitted"], 6)
        self.assertEqual(analytics.get_user_stats()["users_searched_at_least_once"], 1)

    def test_db_failure_returns_empty_dict(self):
        with mock.patch.object(analytics.database, "get_connection", side_effect=RuntimeError("db")):
            self.assertEqual(analytics.get_external_search_stats_in_period(), {})

    def test_event_names_are_consistent_with_ui(self):
        import external_search_ui as ui
        import inspect
        used = set(__import__("re").findall(r'"(ext_[a-z_]+)"', inspect.getsource(ui)))
        self.assertEqual(used, set(analytics.EXT_EVENT_TYPES))


class DashboardTests(Base):
    def test_text_both_languages(self):
        add("ext_search_submitted", 1); add("ext_search_completed", 1); add("ext_pdf_requested", 1); add("ext_pdf_sent", 1)
        LANG[5], LANG[6] = "fa", "en"
        fa = dash._build_external_stats_text("all", 5)
        en = dash._build_external_stats_text("week", 6)
        self.assertIn("آمار جست‌وجوی خارجی", fa); self.assertIn("External Search Stats", en)
        self.assertIn("نرخ موفقیت PDF", fa); self.assertIn("PDF success rate", en)
        self.assertIn("100.0%", fa); self.assertIn("Last 7 days", en)
        self.assertNotIn("🔭", fa + en)

    def test_all_strings_bilingual(self):
        for k, v in dash.STRINGS.items():
            self.assertEqual(set(v), {"fa", "en"}, k)

    def test_main_keyboard_and_callback(self):
        data = [b.callback_data for row in dash.analytics_main_keyboard(1).keyboard for b in row]
        self.assertIn("adm_anl:ext:all", data)
        self.assertTrue(all(len(d.encode()) <= 64 for d in data))
        sent = []

        class Bot:
            def answer_callback_query(self, *a, **k): pass
            def edit_message_text(self, text, **k): sent.append((text, k["reply_markup"]))
            def send_message(self, *a, **k): sent.append(a)
        add("ext_search_submitted", 1)
        cbq = pytypes.SimpleNamespace(id="1", data="adm_anl:ext:month", from_user=pytypes.SimpleNamespace(id=1),
                                      message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=1), message_id=2))
        self.assertTrue(dash.handle_analytics_callback(Bot(), cbq))
        text, markup = sent[0]
        self.assertIn("External Search Stats", text)
        cbs = [b.callback_data for row in markup.keyboard for b in row]
        self.assertIn("adm_anl:ext:all", cbs); self.assertIn("adm_anl:main", cbs)

    def test_existing_sections_still_work(self):
        for fn in (dash._build_overview_text, dash._build_retention_text):
            self.assertTrue(fn(1))
        self.assertTrue(dash._build_search_stats_text("all", 1))
        self.assertTrue(dash._build_delivery_stats_text("all", 1))


if __name__ == "__main__":
    unittest.main()
