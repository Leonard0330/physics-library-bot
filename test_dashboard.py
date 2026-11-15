"""
test_dashboard.py — تست‌های داشبورد Analytics و ارتباط آن با پنل ادمین

اجرا (کنار database_mini.py که به نام database.py کپی شده):
    python test_dashboard.py

پوشش:
  1. دسترسی فقط ادمین
  2. هر بخش داشبورد + دیتابیس خالی
  3. فیلتر بازه‌های زمانی و مرز «امروز/دیروز»
  4. گزارش امروز (on-demand)
  5. Retention
  6. سازگاری با ربات و پنل ادمین (زبان، دکمه‌ها)
  7. گزینه‌ی گیرنده‌ی بکاپ/ریستور (فقط من / همه ادمین‌ها)

نکته: گزارش روزانه‌ی خودکار (scheduler) از analytics_dashboard حذف شده است؛
گزارش فقط با دکمه‌ی «گزارش امروز» (on-demand) ارسال می‌شود.
"""

import contextlib
import os
import sqlite3
import sys
import tempfile
import time
import types as pytypes
import unittest
from unittest.mock import MagicMock, patch

# ── telebot stub ──────────────────────────────────────────────────────────────
_tb = pytypes.ModuleType("telebot")
_tb_types = pytypes.ModuleType("telebot.types")


class _IKM:
    def __init__(self, row_width=1):
        self.keyboard = []

    def add(self, *btns):
        self.keyboard.append(list(btns))

    def row(self, *btns):
        self.keyboard.append(list(btns))


class _IKB:
    def __init__(self, text="", callback_data=None):
        self.text = text
        self.callback_data = callback_data


class _KB:
    def __init__(self, text=""):
        self.text = text


class _RKM:
    def __init__(self, **kwargs):
        self.keyboard = []

    def add(self, *btns):
        self.keyboard.append(list(btns))

    def row(self, *btns):
        self.keyboard.append(list(btns))


for _cls_name in ["CallbackQuery", "Message", "Chat", "User"]:
    setattr(_tb_types, _cls_name, type(_cls_name, (), {}))

_tb_types.InlineKeyboardMarkup = _IKM
_tb_types.InlineKeyboardButton = _IKB
_tb_types.ReplyKeyboardMarkup = _RKM
_tb_types.KeyboardButton = _KB
_tb.types = _tb_types
sys.modules["telebot"] = _tb
sys.modules["telebot.types"] = _tb_types
# ─────────────────────────────────────────────────────────────────────────────

_tmp_dir = tempfile.mkdtemp()
_TEST_DB = os.path.join(_tmp_dir, "test_dashboard.db")

import database  # noqa: E402

database.DB_PATH = _TEST_DB

import analytics  # noqa: E402
import analytics_dashboard  # noqa: E402


def _fresh_db():
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    database.DB_PATH = _TEST_DB
    database.init_db()


def _flush():
    """منتظر می‌مانیم worker thread رویدادهای queue را در دیتابیس بنویسد."""
    deadline = time.time() + 5
    while time.time() < deadline:
        if analytics._event_queue.empty():
            break
        time.sleep(0.05)
    time.sleep(0.2)


def _make_callback(data: str, user_id: int = 999001) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.id = "cb_id_123"
    cb.from_user.id = user_id
    cb.message.chat.id = user_id
    cb.message.message_id = 42
    return cb


def _make_message(text: str, user_id: int = 999001) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.id = user_id
    return msg


@contextlib.contextmanager
def _connect(path: str = None):
    """اتصال SQLite که commit می‌کند و حتماً می‌بندد (with خودِ sqlite3 اتصال را نمی‌بندد)."""
    conn = sqlite3.connect(path or _TEST_DB)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sql(query: str, params=()):
    with _connect() as conn:
        conn.execute(query, params)


# ── 1. دسترسی ─────────────────────────────────────────────────────────────────

class TestDashboardAccess(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        database.upsert_user(901, "admin_user", "Admin")
        database.set_admin(901, True)
        database.upsert_user(902, "regular", "Regular")

    def test_admin_can_open_dashboard_callback(self):
        import admin as adm
        result = adm.handle_admin_callback(MagicMock(), _make_callback("adm_anl:overview", 901))
        self.assertTrue(result)

    def test_non_admin_rejected_for_every_section(self):
        import admin as adm
        for data in ["adm_anl:main", "adm_anl:users:all", "adm_anl:search:today",
                     "adm_anl:delivery:week", "adm_anl:retention", "adm_anl:growth:day",
                     "adm_anl:today_report"]:
            bot = MagicMock()
            result = adm.handle_admin_callback(bot, _make_callback(data, 902))
            self.assertFalse(result, f"غیرادمین نباید به {data} دسترسی داشته باشد")
            bot.send_message.assert_not_called()
            bot.edit_message_text.assert_not_called()


# ── 2. بخش‌های داشبورد ────────────────────────────────────────────────────────

class TestDashboardSections(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        database.upsert_user(801, "u1", "U1")
        database.upsert_user(802, "u2", "U2")
        analytics.log_search_submitted(801, has_filters=False)
        analytics.log_search_completed(801, result_count=3)
        analytics.log_search_submitted(802, has_filters=True)
        analytics.log_search_no_results(802, has_filters=True)
        analytics.log_file_requested(801, resource_id=1, resource_type="book")
        analytics.log_file_sent(801, resource_id=1, resource_type="book")
        analytics.log_file_requested(802, resource_id=2, resource_type="article")
        analytics.log_file_send_failed(802, resource_id=2, resource_type="article")
        _flush()

    def _run(self, data: str):
        bot = MagicMock()
        result = analytics_dashboard.handle_analytics_callback(bot, _make_callback(data, 801))
        return bot, result

    def _text(self, bot) -> str:
        return bot.edit_message_text.call_args[0][0]

    def test_overview(self):
        bot, result = self._run("adm_anl:overview")
        self.assertTrue(result)
        bot.edit_message_text.assert_called_once()
        self.assertIn("خلاصه کلی", self._text(bot))

    def test_users_all_periods(self):
        for p in ["all", "today", "week", "month", "quarter"]:
            bot, result = self._run(f"adm_anl:users:{p}")
            self.assertTrue(result, p)
        bot, _ = self._run("adm_anl:users:all")
        self.assertIn("کاربران", self._text(bot))

    def test_search_section_numbers(self):
        bot, result = self._run("adm_anl:search:all")
        self.assertTrue(result)
        text = self._text(bot)
        self.assertIn("جستجو", text)
        # 2 submitted, 1 completed, 1 no-result
        s = analytics.get_search_stats_in_period(days=None)
        self.assertEqual(s["total_submitted"], 2)
        self.assertEqual(s["total_completed"], 1)
        self.assertEqual(s["total_no_results"], 1)

    def test_delivery_section_numbers(self):
        bot, result = self._run("adm_anl:delivery:all")
        self.assertTrue(result)
        d = analytics.get_delivery_stats_in_period(days=None)
        self.assertEqual((d["total_requested"], d["total_sent"], d["total_failed"]), (2, 1, 1))
        self.assertEqual(d["success_rate_pct"], 50.0)

    def test_retention_growth_main(self):
        for data in ["adm_anl:retention", "adm_anl:growth:day", "adm_anl:growth:week",
                     "adm_anl:growth:month", "adm_anl:main"]:
            _, result = self._run(data)
            self.assertTrue(result, data)

    def test_retention_text_title(self):
        bot, _ = self._run("adm_anl:retention")
        self.assertIn("Retention", self._text(bot))

    def test_unknown_section_returns_false(self):
        _, result = self._run("adm_anl:nonexistent_section")
        self.assertFalse(result)

    def test_foreign_prefix_returns_false(self):
        _, result = self._run("other:overview")
        self.assertFalse(result)


class TestEmptyDatabase(unittest.TestCase):
    """دیتابیس خالی نباید crash کند."""

    def setUp(self):
        _fresh_db()

    def _run(self, data: str):
        bot = MagicMock()
        try:
            analytics_dashboard.handle_analytics_callback(bot, _make_callback(data))
        except Exception as e:  # pragma: no cover
            self.fail(f"crash با دیتابیس خالی برای {data}: {e}")
        bot.edit_message_text.assert_called_once()

    def test_all_sections_empty(self):
        for data in ["adm_anl:overview", "adm_anl:users:all", "adm_anl:search:all",
                     "adm_anl:delivery:all", "adm_anl:retention", "adm_anl:growth:day"]:
            with self.subTest(data=data):
                self._run(data)

    def test_overview_text_is_str(self):
        self.assertIsInstance(analytics_dashboard._build_overview_text(0), str)

    def test_retention_no_data_graceful(self):
        self.assertIn("داده کافی", analytics_dashboard._build_retention_text(0))


# ── 3. بازه‌های زمانی ─────────────────────────────────────────────────────────

class TestDateFilters(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        for i in range(3):
            analytics.log_search_submitted(700 + i, has_filters=False)
            analytics.log_search_completed(700 + i, result_count=1)
        _flush()

    def test_today_and_all_nonzero(self):
        self.assertEqual(analytics.get_search_stats_in_period(days=1)["total_submitted"], 3)
        self.assertEqual(analytics.get_search_stats_in_period(days=None)["total_submitted"], 3)

    def test_period_text_contains_label(self):
        for period in ["today", "week", "month", "quarter", "all"]:
            text = analytics_dashboard._build_search_stats_text(period, 0)
            self.assertIn(analytics_dashboard._period_label(period, 0), text, period)

    def test_period_keyboard_buttons(self):
        kb = analytics_dashboard.period_filter_keyboard("search", "all", 0)
        buttons = [b.text for row in kb.keyboard for b in row]
        self.assertTrue(any("امروز" in b for b in buttons))
        self.assertTrue(any("همه" in b for b in buttons))

    def test_current_period_is_marked(self):
        kb = analytics_dashboard.period_filter_keyboard("users", "week", 0)
        marked = [b.text for row in kb.keyboard for b in row if b.text.startswith("●")]
        self.assertEqual(len(marked), 1)


class TestPeriodWindows(unittest.TestCase):

    def setUp(self):
        _fresh_db()

    def test_period_where_today(self):
        clause, params = analytics._period_where("created_at", 1, 0)
        self.assertIn(">=", clause)
        self.assertNotIn("<", clause)
        self.assertEqual(params, ["-1 days"])

    def test_period_where_yesterday(self):
        clause, params = analytics._period_where("created_at", 1, 1)
        self.assertIn(">=", clause)
        self.assertIn("<", clause)
        self.assertEqual(params, ["-2 days", "-1 days"])

    def test_period_where_all(self):
        self.assertEqual(analytics._period_where("created_at", None), ("", []))

    def test_yesterday_window_does_not_overlap_today(self):
        database.upsert_user(901, "today_user", "TodayUser")
        self.assertEqual(analytics.get_new_users_in_period(days=1, offset_days=0), 1)
        self.assertEqual(analytics.get_new_users_in_period(days=1, offset_days=1), 0)

    def test_event_timestamp_format_boundary(self):
        """
        regression: analytics_events.created_at فرمت ISO ('T…Z') دارد ولی
        datetime('now') فرمت 'YYYY-MM-DD HH:MM:SS'. مقایسه‌ی رشته‌ای باعث
        می‌شد رویداد ۳۰ ساعت پیش در «۲۴ ساعت اخیر» شمرده شود.
        """
        with _connect() as conn:
            conn.execute(
                "INSERT INTO analytics_events (event_type, user_id, created_at) "
                "VALUES ('search_submitted', 1, strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 hours'))"
            )
            conn.execute(
                "INSERT INTO analytics_events (event_type, user_id, created_at) "
                "VALUES ('search_submitted', 2, strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 hours'))"
            )
        last_24h = analytics.get_search_stats_in_period(days=1)["total_submitted"]
        self.assertEqual(last_24h, 1, "فقط رویداد ۱ ساعت پیش باید در ۲۴ ساعت اخیر باشد")
        yesterday = analytics.get_search_stats_in_period(days=1, offset_days=1)["total_submitted"]
        self.assertEqual(yesterday, 1, "رویداد ۳۰ ساعت پیش باید در «دیروز» باشد")


class TestReportingTimezone(unittest.TestCase):

    def test_default_is_utc(self):
        self.assertEqual(analytics_dashboard.REPORTING_TZ_NAME, "UTC")
        self.assertIsNotNone(analytics_dashboard._get_reporting_tz())

    def test_invalid_tz_falls_back(self):
        original = analytics_dashboard.REPORTING_TZ_NAME
        analytics_dashboard.REPORTING_TZ_NAME = "Invalid/Timezone"
        try:
            self.assertIsNotNone(analytics_dashboard._get_reporting_tz())
            self.assertRegex(analytics_dashboard._report_date_for_now(), r"^\d{4}-\d{2}-\d{2}$")
        finally:
            analytics_dashboard.REPORTING_TZ_NAME = original

    def test_report_date_format(self):
        self.assertRegex(analytics_dashboard._report_date_for_now(), r"^\d{4}-\d{2}-\d{2}$")


# ── 4. گزارش امروز (on-demand) ────────────────────────────────────────────────

class TestTodayReport(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        database.upsert_user(601, "admin1", "Admin1")
        database.set_admin(601, True)

    def test_text_structure(self):
        text = analytics_dashboard._build_daily_report_text(analytics_dashboard._report_date_for_now(), 601)
        for word in ("گزارش روزانه", "کاربران", "جستجو", "تحویل"):
            self.assertIn(word, text)

    def test_send_report_to_admin_sends_one_html_message(self):
        bot = MagicMock()
        analytics_dashboard.send_report_to_admin(bot, 601)
        bot.send_message.assert_called_once()
        args, kwargs = bot.send_message.call_args
        self.assertEqual(args[0], 601)
        self.assertEqual(kwargs.get("parse_mode"), "HTML")

    def test_send_failure_does_not_raise(self):
        bot = MagicMock()
        bot.send_message.side_effect = Exception("Telegram 500")
        try:
            analytics_dashboard.send_report_to_admin(bot, 601)
        except Exception as e:  # pragma: no cover
            self.fail(f"نباید exception بدهد: {e}")

    def test_callback_today_report_goes_to_requesting_admin(self):
        bot = MagicMock()
        cb = _make_callback("adm_anl:today_report", 601)
        self.assertTrue(analytics_dashboard.handle_analytics_callback(bot, cb))
        bot.send_message.assert_called_once()
        self.assertEqual(bot.send_message.call_args[0][0], 601)

    def test_delta_formatting(self):
        d = analytics_dashboard._delta
        self.assertEqual(d(10, 8), " ↑2")
        self.assertEqual(d(5, 8), " ↓3")
        self.assertEqual(d(8, 8), " ═")
        self.assertEqual(d(5, 0), "")


class TestEditOrSend(unittest.TestCase):

    def test_not_modified_does_not_send_new_message(self):
        bot = MagicMock()
        bot.edit_message_text.side_effect = Exception(
            "Bad Request: message is not modified: specified new message content ...")
        analytics_dashboard._edit_or_send(bot, _make_callback("adm_anl:main"), "x", MagicMock())
        bot.send_message.assert_not_called()

    def test_other_edit_errors_fall_back_to_new_message(self):
        bot = MagicMock()
        bot.edit_message_text.side_effect = Exception("Bad Request: message to edit not found")
        analytics_dashboard._edit_or_send(bot, _make_callback("adm_anl:main"), "x", MagicMock())
        bot.send_message.assert_called_once()


# ── 5. Retention ──────────────────────────────────────────────────────────────

class TestRetention(unittest.TestCase):

    def setUp(self):
        _fresh_db()

    def _user(self, uid: int, days_ago: float):
        database.upsert_user(uid, f"r{uid}", f"R{uid}")
        _sql("UPDATE users SET created_at = datetime('now', ?) WHERE user_id = ?",
             (f"-{days_ago} days", uid))

    def _event(self, uid: int, days_ago: float):
        _sql("INSERT INTO analytics_events (event_type, user_id, created_at) "
             "VALUES ('user_started', ?, datetime('now', ?))", (uid, f"-{days_ago} days"))

    def test_no_eligible_users(self):
        r = analytics.get_retention_cohort(1)
        self.assertEqual(r["eligible_users"], 0)
        self.assertIsNone(r["retention_rate_pct"])

    def test_incomplete_cohorts_excluded(self):
        self._user(1001, 0.5)
        self.assertEqual(analytics.get_retention_cohort(1)["eligible_users"], 0)
        self._user(1005, 5)
        self.assertEqual(analytics.get_retention_cohort(7)["eligible_users"], 0)
        self._user(1007, 25)
        self.assertEqual(analytics.get_retention_cohort(30)["eligible_users"], 0)

    def test_complete_cohorts_included(self):
        self._user(1002, 2.5)
        self.assertEqual(analytics.get_retention_cohort(1)["eligible_users"], 1)
        self._user(1006, 9)
        self.assertEqual(analytics.get_retention_cohort(7)["eligible_users"], 1)

    def test_retained_inside_window(self):
        self._user(1003, 2.5)
        self._event(1003, 1.5)      # [day1, day2) = [1.5, 0.5) روز پیش
        r = analytics.get_retention_cohort(1)
        self.assertEqual(r["retained_users"], 1)
        self.assertEqual(r["retention_rate_pct"], 100.0)

    def test_retained_with_iso_event_timestamp(self):
        """رویدادهایی که worker با فرمت ISO می‌نویسد هم باید درست شمرده شوند."""
        self._user(1010, 2.5)
        _sql("INSERT INTO analytics_events (event_type, user_id, created_at) VALUES "
             "('user_started', 1010, strftime('%Y-%m-%dT%H:%M:%fZ','now','-1.5 days'))")
        self.assertEqual(analytics.get_retention_cohort(1)["retained_users"], 1)

    def test_activity_outside_window_not_retained(self):
        self._user(1004, 2.5)
        self._event(1004, 2.5)
        r = analytics.get_retention_cohort(1)
        self.assertEqual((r["eligible_users"], r["retained_users"]), (1, 0))

    def test_users_without_created_at_excluded(self):
        database.upsert_user(1008, "hist", "Hist")
        _sql("UPDATE users SET created_at = NULL WHERE user_id = 1008")
        self.assertEqual(analytics.get_retention_cohort(1)["eligible_users"], 0)

    def test_text_mentions_incomplete_cohorts(self):
        self.assertIn("ناقص", analytics_dashboard._build_retention_text(0))


class TestPeriodQueryHelpers(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        for i in range(5):
            database.upsert_user(500 + i, f"u{i}", f"U{i}")
            analytics.log_search_submitted(500 + i, has_filters=False)
        _flush()

    def test_counts(self):
        self.assertEqual(analytics.get_new_users_in_period(days=1), 5)
        self.assertEqual(analytics.get_active_users_in_period(days=1), 5)
        self.assertEqual(analytics.get_search_stats_in_period(days=1)["total_submitted"], 5)
        self.assertEqual(analytics.get_search_stats_in_period(days=None)["total_submitted"], 5)

    def test_delivery_empty_period(self):
        d = analytics.get_delivery_stats_in_period(days=1)
        self.assertEqual(d["total_requested"], 0)
        self.assertEqual(d["success_rate_pct"], 0.0)


# ── 6. سازگاری با ربات و پنل ادمین ────────────────────────────────────────────

class TestBotCompatibility(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        database.upsert_user(950, "adm", "Adm")
        database.set_admin(950, True)

    def test_admin_keyboard_has_analytics_button(self):
        import admin as adm
        kb = adm.admin_keyboard("fa")
        buttons = [getattr(b, "text", str(b)) for row in kb.keyboard for b in row]
        self.assertTrue(any("Analytics" in b or "داشبورد" in b for b in buttons), buttons)

    def test_analytics_button_opens_dashboard_with_admin_uid(self):
        """admin.py باید uid را به داشبورد بدهد تا زبان ادمین رعایت شود."""
        import admin as adm
        bot = MagicMock()
        msg = _make_message(adm.T["btn_analytics"]["fa"], 950)
        with patch.object(analytics_dashboard, "show_analytics_main") as m:
            handled = adm.handle_admin_text(bot, msg)
        self.assertTrue(handled)
        m.assert_called_once()
        self.assertEqual(m.call_args[0][2] if len(m.call_args[0]) > 2 else m.call_args[1].get("uid"), 950)

    def test_dashboard_main_menu_uses_uid_language(self):
        bot = MagicMock()
        with patch.object(database, "get_user_lang", return_value="en"):
            analytics_dashboard.show_analytics_main(bot, 950, 950)
        text = bot.send_message.call_args[0][1]
        self.assertIn("Analytics Dashboard", text)

    def test_back_button_returns_to_admin_panel(self):
        kb = analytics_dashboard.analytics_main_keyboard(950)
        datas = [b.callback_data for row in kb.keyboard for b in row]
        self.assertIn("adm_open_panel", datas)

    def test_callback_data_within_telegram_limit(self):
        kbs = [analytics_dashboard.analytics_main_keyboard(950),
               analytics_dashboard.period_filter_keyboard("delivery", "quarter", 950),
               analytics_dashboard.growth_period_keyboard("month", 950),
               analytics_dashboard.back_to_main_keyboard(950)]
        for kb in kbs:
            for row in kb.keyboard:
                for b in row:
                    self.assertLessEqual(len(b.callback_data.encode()), 64, b.callback_data)

    def test_analytics_prefix_not_shadowed_by_other_admin_prefixes(self):
        others = ["adm_rtype:", "adm_lang:", "adm_field:", "adm_confirm:", "adm_csv_import:",
                  "adm_pnd:", "adm_open_panel", "adm_bk:", "adm_rs:"]
        for p in others:
            self.assertFalse("adm_anl:".startswith(p) or p.startswith("adm_anl:"), p)

    def test_user_started_and_search_events_recorded(self):
        analytics.log_user_started(950, is_new=True)
        analytics.log_search_submitted(950, has_filters=False)
        _flush()
        self.assertEqual(analytics.get_search_count_for_user(950), 1)


# ── 7. گیرنده‌ی بکاپ / ریستور ─────────────────────────────────────────────────

class TestBackupRestoreRecipients(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        for uid in (1, 2, 3):
            database.upsert_user(uid, f"a{uid}", f"A{uid}")
            database.set_admin(uid, True)
        import admin as adm
        self.adm = adm
        adm._restore_sessions.clear()

    def tearDown(self):
        for sess in list(self.adm._restore_sessions.values()):
            t = sess.get("timer")
            if t:
                t.cancel()
        self.adm._restore_sessions.clear()
        if self.adm._backup_restore_lock.locked():
            self.adm._backup_restore_lock.release()

    def _targets(self, bot):
        return sorted(c[0][0] for c in bot.send_document.call_args_list)

    def test_broadcast_all_admins_by_default(self):
        bot = MagicMock()
        path = self.adm.broadcast_backup_to_admins(bot, "backup_caption", time="t")
        self.assertEqual(self._targets(bot), [1, 2, 3])
        os.unlink(path)

    def test_broadcast_only_requester(self):
        bot = MagicMock()
        path = self.adm.broadcast_backup_to_admins(bot, "backup_caption", recipients=[2], time="t")
        self.assertEqual(self._targets(bot), [2])
        os.unlink(path)

    def test_backup_command_asks_scope_first_without_sending_file(self):
        bot = MagicMock()
        self.adm.handle_backup_command(bot, _make_message("/backup", 1))
        bot.send_document.assert_not_called()
        kb = bot.send_message.call_args[1]["reply_markup"]
        datas = [b.callback_data for row in kb.keyboard for b in row]
        self.assertEqual(datas, ["adm_bk:me", "adm_bk:all", "adm_bk:cancel"])

    def test_backup_callback_me_sends_only_to_requester(self):
        bot = MagicMock()
        self.assertTrue(self.adm.handle_admin_callback(bot, _make_callback("adm_bk:me", 2)))
        self.assertEqual(self._targets(bot), [2])

    def test_backup_callback_all_sends_to_everyone(self):
        bot = MagicMock()
        self.adm.handle_admin_callback(bot, _make_callback("adm_bk:all", 2))
        self.assertEqual(self._targets(bot), [1, 2, 3])

    def test_backup_callback_cancel_sends_nothing(self):
        bot = MagicMock()
        self.adm.handle_admin_callback(bot, _make_callback("adm_bk:cancel", 2))
        bot.send_document.assert_not_called()

    def test_backup_lock_released_after_run(self):
        self.adm.handle_admin_callback(MagicMock(), _make_callback("adm_bk:me", 1))
        self.assertFalse(self.adm._backup_restore_lock.locked())

    def test_non_admin_cannot_use_backup_callbacks(self):
        database.upsert_user(77, "x", "X")
        bot = MagicMock()
        self.assertFalse(self.adm.handle_admin_callback(bot, _make_callback("adm_bk:all", 77)))
        bot.send_document.assert_not_called()

    def test_restore_flow_scope_then_file_prompt(self):
        adm = self.adm
        bot = MagicMock()
        adm.handle_restore_command(bot, _make_message("/restore", 1))
        self.assertEqual(adm._restore_sessions[1]["step"], "wait_scope")

        # قبل از انتخاب گیرنده، ارسال فایل پذیرفته نمی‌شود
        doc_msg = _make_message("", 1)
        doc_msg.document.file_name = "x.db"
        self.assertFalse(adm.handle_restore_document(bot, doc_msg))

        adm.handle_admin_callback(bot, _make_callback("adm_rs:me", 1))
        self.assertEqual(adm._restore_sessions[1]["step"], "wait_file")
        self.assertEqual(adm._restore_sessions[1]["scope"], "me")

        adm.handle_cancel_command(bot, _make_message("/cancel", 1))
        self.assertNotIn(1, adm._restore_sessions)
        self.assertFalse(adm._backup_restore_lock.locked())

    def test_restore_scope_cancel_releases_lock(self):
        adm = self.adm
        bot = MagicMock()
        adm.handle_restore_command(bot, _make_message("/restore", 1))
        adm.handle_admin_callback(bot, _make_callback("adm_rs:cancel", 1))
        self.assertNotIn(1, adm._restore_sessions)
        self.assertFalse(adm._backup_restore_lock.locked())

    def test_restore_scope_callback_without_session_is_rejected(self):
        bot = MagicMock()
        self.adm.handle_admin_callback(bot, _make_callback("adm_rs:all", 1))
        self.assertIn("show_alert", bot.answer_callback_query.call_args[1])

    def test_restore_emergency_backup_respects_scope(self):
        """ریستور با scope=me: بکاپ اضطراری فقط برای ریستورکننده ارسال شود."""
        adm = self.adm
        bot = MagicMock()
        adm.handle_restore_command(bot, _make_message("/restore", 2))
        adm.handle_admin_callback(bot, _make_callback("adm_rs:me", 2))

        # فایل .db معتبر با جدول‌های لازم
        src = os.path.join(_tmp_dir, "to_restore.db")
        with _connect(src) as c:
            c.executescript("CREATE TABLE IF NOT EXISTS books(id INTEGER);"
                            "CREATE TABLE IF NOT EXISTS users(user_id INTEGER);"
                            "CREATE TABLE IF NOT EXISTS download_logs(id INTEGER);")
        with open(src, "rb") as f:
            raw = f.read()
        bot.get_file.return_value = MagicMock(file_path="p")
        bot.download_file.return_value = raw
        msg = _make_message("", 2)
        msg.document.file_name = "backup.db"
        msg.document.file_id = "fid"
        self.assertTrue(adm.handle_restore_document(bot, msg))

        self.assertEqual(self._targets(bot), [2])
        self.assertEqual(adm._restore_sessions[2]["step"], "wait_confirm")
        adm.handle_cancel_command(bot, _make_message("/cancel", 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
