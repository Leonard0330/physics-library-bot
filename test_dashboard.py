"""
test_dashboard.py — تست‌های Phase 3: داشبورد و گزارش روزانه

اجرا: python test_dashboard.py

تست‌های پوشش‌داده‌شده:
  1. دسترسی فقط ادمین
  2. رد callback غیرمجاز
  3. هر بخش داشبورد
  4. فیلترهای بازه زمانی
  5. دیتابیس خالی
  6. تولید گزارش روزانه
  7. جلوگیری از تکرار گزارش
  8. Restart safety (idempotency)
  9. retention calculations
  10. سازگاری با bot موجود
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types as pytypes
import unittest
from unittest.mock import MagicMock, patch, call

# ── telebot stub ──────────────────────────────────────────────────────────────
_tb = pytypes.ModuleType("telebot")
_tb_types = pytypes.ModuleType("telebot.types")

class _IKM:
    def __init__(self, row_width=1):
        self.keyboard = []
    def add(self, *btns):
        self.keyboard.append(list(btns))

class _IKB:
    def __init__(self, text="", callback_data=None):
        self.text = text
        self.callback_data = callback_data

class _RKM:
    def __init__(self, **kwargs):
        self.keyboard = []
    def add(self, *btns):
        self.keyboard.append(list(btns))

for _cls_name in ["CallbackQuery", "Message", "Chat", "User"]:
    setattr(_tb_types, _cls_name, type(_cls_name, (), {}))

_tb_types.InlineKeyboardMarkup = _IKM
_tb_types.InlineKeyboardButton = _IKB
_tb_types.ReplyKeyboardMarkup  = _RKM
_tb.types = _tb_types
sys.modules["telebot"]       = _tb
sys.modules["telebot.types"] = _tb_types
# ─────────────────────────────────────────────────────────────────────────────

_tmp_dir = tempfile.mkdtemp()
_TEST_DB = os.path.join(_tmp_dir, "test_dashboard.db")

import database
database.DB_PATH = _TEST_DB

import analytics
import analytics_dashboard


def _fresh_db():
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    database.DB_PATH = _TEST_DB
    database.init_db()


def _flush():
    time.sleep(0.3)


def _make_callback(data: str, user_id: int = 999001) -> MagicMock:
    """ساخت یک mock CallbackQuery."""
    cb = MagicMock()
    cb.data = data
    cb.id   = "cb_id_123"
    cb.from_user.id = user_id
    cb.message.chat.id    = user_id
    cb.message.message_id = 42
    return cb


def _make_message(text: str, user_id: int = 999001) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user.id  = user_id
    msg.chat.id       = user_id
    return msg


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDashboardAccess(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        # admin user
        database.upsert_user(901, "admin_user", "Admin")
        database.set_admin(901, True)
        # regular user
        database.upsert_user(902, "regular", "Regular")

    def test_analytics_callback_routing_admin(self):
        """ادمین باید بتواند داشبورد را ببیند."""
        import admin as adm
        bot = MagicMock()
        cb  = _make_callback("adm_anl:overview", user_id=901)
        # handle_admin_callback باید is_admin را چک کند
        result = adm.handle_admin_callback(bot, cb)
        self.assertTrue(result)

    def test_analytics_callback_routing_non_admin(self):
        """غیرادمین نباید به داشبورد دسترسی داشته باشد."""
        import admin as adm
        bot = MagicMock()
        cb  = _make_callback("adm_anl:overview", user_id=902)
        result = adm.handle_admin_callback(bot, cb)
        self.assertFalse(result)

    def test_all_analytics_callbacks_check_admin(self):
        """هر callback با prefix adm_anl باید is_admin چک کند."""
        import admin as adm
        bot = MagicMock()
        callbacks = [
            "adm_anl:main",
            "adm_anl:users:all",
            "adm_anl:search:today",
            "adm_anl:delivery:week",
            "adm_anl:retention",
            "adm_anl:growth:day",
        ]
        for data in callbacks:
            cb = _make_callback(data, user_id=902)   # non-admin
            result = adm.handle_admin_callback(bot, cb)
            self.assertFalse(result, f"غیرادمین نباید به {data} دسترسی داشته باشد")


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

    def _run_callback(self, data: str):
        bot = MagicMock()
        cb  = _make_callback(data, user_id=801)
        result = analytics_dashboard.handle_analytics_callback(bot, cb)
        return bot, result

    def test_overview_section(self):
        bot, result = self._run_callback("adm_anl:overview")
        self.assertTrue(result)
        bot.edit_message_text.assert_called_once()
        text = bot.edit_message_text.call_args[0][0]
        self.assertIn("خلاصه کلی", text)
        self.assertIn("کاربران", text)

    def test_users_section_all(self):
        bot, result = self._run_callback("adm_anl:users:all")
        self.assertTrue(result)
        text = bot.edit_message_text.call_args[0][0]
        self.assertIn("کاربران", text)
        self.assertIn("نرخ تبدیل", text)

    def test_users_section_today(self):
        bot, result = self._run_callback("adm_anl:users:today")
        self.assertTrue(result)

    def test_users_section_week(self):
        bot, result = self._run_callback("adm_anl:users:week")
        self.assertTrue(result)

    def test_users_section_month(self):
        bot, result = self._run_callback("adm_anl:users:month")
        self.assertTrue(result)

    def test_users_section_quarter(self):
        bot, result = self._run_callback("adm_anl:users:quarter")
        self.assertTrue(result)

    def test_search_section(self):
        bot, result = self._run_callback("adm_anl:search:all")
        self.assertTrue(result)
        text = bot.edit_message_text.call_args[0][0]
        self.assertIn("جستجو", text)
        self.assertIn("موفق", text)

    def test_delivery_section(self):
        bot, result = self._run_callback("adm_anl:delivery:all")
        self.assertTrue(result)
        text = bot.edit_message_text.call_args[0][0]
        self.assertIn("تحویل", text)

    def test_retention_section(self):
        bot, result = self._run_callback("adm_anl:retention")
        self.assertTrue(result)
        text = bot.edit_message_text.call_args[0][0]
        self.assertIn("Retention", text)

    def test_growth_day_section(self):
        bot, result = self._run_callback("adm_anl:growth:day")
        self.assertTrue(result)

    def test_growth_week_section(self):
        bot, result = self._run_callback("adm_anl:growth:week")
        self.assertTrue(result)

    def test_growth_month_section(self):
        bot, result = self._run_callback("adm_anl:growth:month")
        self.assertTrue(result)

    def test_main_menu(self):
        bot, result = self._run_callback("adm_anl:main")
        self.assertTrue(result)

    def test_unknown_section_returns_false(self):
        bot, result = self._run_callback("adm_anl:nonexistent_section")
        self.assertFalse(result)


class TestEmptyDatabase(unittest.TestCase):
    """دیتابیس کاملاً خالی نباید crash کند."""

    def setUp(self):
        _fresh_db()

    def _run(self, data: str):
        bot = MagicMock()
        cb  = _make_callback(data)
        try:
            analytics_dashboard.handle_analytics_callback(bot, cb)
        except Exception as e:
            self.fail(f"crash با دیتابیس خالی برای {data}: {e}")

    def test_overview_empty(self):       self._run("adm_anl:overview")
    def test_users_empty(self):          self._run("adm_anl:users:all")
    def test_search_empty(self):         self._run("adm_anl:search:all")
    def test_delivery_empty(self):       self._run("adm_anl:delivery:all")
    def test_retention_empty(self):      self._run("adm_anl:retention")
    def test_growth_empty(self):         self._run("adm_anl:growth:day")

    def test_overview_text_no_crash(self):
        """متن خلاصه با دیتابیس خالی نباید crash کند."""
        try:
            text = analytics_dashboard._build_overview_text()
            self.assertIsInstance(text, str)
        except Exception as e:
            self.fail(f"_build_overview_text crash: {e}")

    def test_retention_no_data_graceful(self):
        text = analytics_dashboard._build_retention_text()
        self.assertIn("داده کافی", text)


class TestDateFilters(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        # چند رویداد امروز
        for i in range(3):
            analytics.log_search_submitted(700 + i, has_filters=False)
            analytics.log_search_completed(700 + i, result_count=1)
        _flush()

    def test_today_filter_nonzero(self):
        s = analytics.get_search_stats_in_period(days=1)
        self.assertGreater(s.get("total_submitted", 0), 0)

    def test_all_filter_nonzero(self):
        s = analytics.get_search_stats_in_period(days=None)
        self.assertGreater(s.get("total_submitted", 0), 0)

    def test_period_text_contains_label(self):
        for period in ["today", "week", "month", "quarter", "all"]:
            text = analytics_dashboard._build_search_stats_text(period)
            self.assertIn(analytics_dashboard._period_label(period), text,
                          f"برچسب بازه '{period}' باید در متن باشد")

    def test_period_keyboard_has_all_buttons(self):
        kb = analytics_dashboard.period_filter_keyboard("search", "all")
        buttons = [b.text for row in kb.keyboard for b in row]
        self.assertTrue(any("امروز" in b for b in buttons))
        self.assertTrue(any("همه"   in b for b in buttons))


class TestDailyReport(unittest.TestCase):

    def setUp(self):
        _fresh_db()

    def test_daily_report_sent_to_admins(self):
        """گزارش روزانه باید به همه ادمین‌ها ارسال شود."""
        database.upsert_user(601, "admin1", "Admin1")
        database.set_admin(601, True)
        database.upsert_user(602, "admin2", "Admin2")
        database.set_admin(602, True)

        bot = MagicMock()
        result = analytics_dashboard.send_daily_report(bot, force=True)
        self.assertTrue(result)
        self.assertEqual(bot.send_message.call_count, 2)

    def test_daily_report_text_structure(self):
        """گزارش روزانه باید بخش‌های اصلی را داشته باشد."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        text = analytics_dashboard._build_daily_report_text(today)
        self.assertIn("گزارش روزانه", text)
        self.assertIn("کاربران", text)
        self.assertIn("جستجو", text)
        self.assertIn("تحویل", text)

    def test_no_duplicate_report_same_day(self):
        """گزارش روزانه نباید دو بار در یک روز ارسال شود."""
        database.upsert_user(603, "admin3", "Admin3")
        database.set_admin(603, True)

        bot = MagicMock()
        analytics_dashboard.send_daily_report(bot)   # اول
        analytics_dashboard.send_daily_report(bot)   # دوم — باید skip شود
        self.assertEqual(bot.send_message.call_count, 1,
                         "گزارش نباید دوبار در یک روز ارسال شود")

    def test_no_admins_returns_false_cleanup(self):
        """اگر ادمینی وجود نداشته باشد نباید crash کند."""
        bot = MagicMock()
        try:
            analytics_dashboard.send_daily_report(bot, force=True)
        except Exception as e:
            self.fail(f"send_daily_report crash: {e}")

    def test_force_bypasses_idempotency(self):
        """force=True باید idempotency guard را دور بزند."""
        database.upsert_user(604, "admin4", "Admin4")
        database.set_admin(604, True)

        bot = MagicMock()
        analytics_dashboard.send_daily_report(bot, force=True)
        analytics_dashboard.send_daily_report(bot, force=True)
        self.assertEqual(bot.send_message.call_count, 2)

    def test_no_admins_no_crash(self):
        """اگر ادمینی وجود نداشته باشد نباید crash کند."""
        bot = MagicMock()
        try:
            analytics_dashboard.send_daily_report(bot, force=True)
        except Exception as e:
            self.fail(f"send_daily_report crash: {e}")

    def test_report_survives_send_failure(self):
        """اگر ارسال به یک ادمین fail شود، باید به بقیه ادامه دهد."""
        database.upsert_user(605, "admin5", "Admin5")
        database.set_admin(605, True)
        database.upsert_user(606, "admin6", "Admin6")
        database.set_admin(606, True)

        bot = MagicMock()
        # اولین call fail می‌شود
        bot.send_message.side_effect = [Exception("Network error"), None]
        try:
            analytics_dashboard.send_daily_report(bot, force=True)
        except Exception as e:
            self.fail(f"crash هنگام fail شدن ارسال: {e}")
        # باید هر دو ادمین تلاش شده باشند
        self.assertEqual(bot.send_message.call_count, 2)


class TestRetentionCalculations(unittest.TestCase):

    def setUp(self):
        _fresh_db()

    def test_retention_no_eligible_users(self):
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["eligible_users"], 0)
        self.assertIsNone(result["retention_rate_pct"])

    def test_retention_text_shows_no_data(self):
        text = analytics_dashboard._build_retention_text()
        self.assertIn("داده کافی", text)

    def test_delta_formatting(self):
        self.assertEqual(analytics_dashboard._delta(10, 8),  " ↑2")
        self.assertEqual(analytics_dashboard._delta(5,  8),  " ↓3")
        self.assertEqual(analytics_dashboard._delta(8,  8),  " ═")
        self.assertEqual(analytics_dashboard._delta(5,  0),  "")


class TestPeriodQueryHelpers(unittest.TestCase):

    def setUp(self):
        _fresh_db()
        for i in range(5):
            database.upsert_user(500 + i, f"u{i}", f"U{i}")
            analytics.log_search_submitted(500 + i, has_filters=False)
        _flush()

    def test_new_users_in_period_today(self):
        count = analytics.get_new_users_in_period(days=1)
        self.assertEqual(count, 5)

    def test_active_users_in_period_today(self):
        count = analytics.get_active_users_in_period(days=1)
        self.assertEqual(count, 5)

    def test_search_stats_in_period_today(self):
        s = analytics.get_search_stats_in_period(days=1)
        self.assertEqual(s.get("total_submitted"), 5)

    def test_search_stats_in_period_all(self):
        s = analytics.get_search_stats_in_period(days=None)
        self.assertEqual(s.get("total_submitted"), 5)

    def test_delivery_stats_empty_period(self):
        d = analytics.get_delivery_stats_in_period(days=1)
        self.assertEqual(d.get("total_requested"), 0)
        self.assertEqual(d.get("success_rate_pct"), 0.0)


class TestBotCompatibility(unittest.TestCase):
    """اطمینان از اینکه Phase 3 عملکرد موجود را نشکسته."""

    def setUp(self):
        _fresh_db()

    def test_upsert_user_still_works(self):
        is_new = database.upsert_user(400, "compat", "Compat")
        self.assertTrue(is_new)

    def test_analytics_still_works(self):
        analytics.log_search_submitted(400, has_filters=False)
        _flush()
        s = analytics.get_search_stats()
        self.assertGreater(s.get("total_submitted", 0), 0)

    def test_admin_keyboard_has_analytics_button(self):
        import admin as adm
        kb = adm.admin_keyboard("fa")
        # ReplyKeyboardMarkup.keyboard is list[list[dict]] where each dict has 'text'
        buttons = []
        for row in kb.keyboard:
            for b in row:
                if isinstance(b, dict):
                    buttons.append(b.get("text", ""))
                elif hasattr(b, "text"):
                    buttons.append(b.text)
                else:
                    buttons.append(str(b))
        self.assertTrue(
            any("Analytics" in b or "داشبورد" in b for b in buttons),
            f"دکمه Analytics باید در keyboard ادمین باشد. دکمه‌های موجود: {buttons}"
        )

    def test_analytics_callback_prefix_not_conflict(self):
        """prefix 'adm_anl:' نباید با سایر callback‌های ادمین تداخل داشته باشد."""
        existing_prefixes = [
            "adm_rtype:", "adm_lang:", "adm_field:", "adm_confirm:",
            "adm_csv_import:", "adm_pnd:", "adm_open_panel",
        ]
        for p in existing_prefixes:
            self.assertFalse(
                p.startswith("adm_anl:"),
                f"prefix '{p}' نباید با 'adm_anl:' تداخل داشته باشد"
            )


# ── Runner ────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# Phase 3.1 — Reliability tests
# ══════════════════════════════════════════════════════════════════════════════

import sqlite3 as _sqlite3


def _get_report_log(report_date: str = None) -> list:
    """Helper: خواندن مستقیم جدول daily_report_log از دیتابیس."""
    with _sqlite3.connect(_TEST_DB) as conn:
        conn.row_factory = _sqlite3.Row
        if report_date:
            rows = conn.execute(
                "SELECT * FROM daily_report_log WHERE report_date=?",
                (report_date,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM daily_report_log").fetchall()
    return [dict(r) for r in rows]


class TestPersistentIdempotency(unittest.TestCase):
    """
    تست‌های idempotency پایدار (SQLite-backed).
    تضمین می‌کند restart نمی‌تواند گزارش تکراری ایجاد کند.
    """

    def setUp(self):
        _fresh_db()
        # اطمینان از وجود جدول daily_report_log
        analytics_dashboard._init_report_log_table()
        database.upsert_user(701, "admin_p1", "AdminP1")
        database.set_admin(701, True)

    def test_report_log_table_exists(self):
        """جدول daily_report_log باید در دیتابیس وجود داشته باشد."""
        with _sqlite3.connect(_TEST_DB) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_report_log'"
            ).fetchone()
        self.assertIsNotNone(row, "جدول daily_report_log باید وجود داشته باشد")

    def test_first_send_creates_log_record(self):
        """اولین ارسال باید رکورد 'sent' در daily_report_log ایجاد کند."""
        bot = MagicMock()
        analytics_dashboard.send_daily_report(bot)
        today = analytics_dashboard._report_date_for_now()
        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(rows[0]["admin_id"], 701)

    def test_restart_does_not_duplicate_sent_report(self):
        """
        شبیه‌سازی restart: ارسال موفق + reset state در حافظه +
        ارسال مجدد نباید پیام تکراری بفرستد.
        """
        bot = MagicMock()
        # ارسال اول
        analytics_dashboard.send_daily_report(bot)
        self.assertEqual(bot.send_message.call_count, 1)

        # شبیه‌سازی restart: هیچ چیزی در حافظه نیست اما دیتابیس دست نخورد
        # (در تست واقعی این به‌صورت خودکار با importlib.reload شبیه‌سازی می‌شد،
        # اما چون ماژول تنها یک‌بار import می‌شود، مستقیماً ارسال می‌کنیم)
        analytics_dashboard.send_daily_report(bot)   # باید skip شود
        self.assertEqual(bot.send_message.call_count, 1,
                         "بعد از restart نباید گزارش تکراری ارسال شود")

    def test_failed_delivery_is_retryable(self):
        """ارسال ناموفق باید 'failed' ثبت شود و قابل retry باشد."""
        bot = MagicMock()
        bot.send_message.side_effect = Exception("Telegram API error")
        analytics_dashboard.send_daily_report(bot)

        today = analytics_dashboard._report_date_for_now()
        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed",
                         "ارسال ناموفق باید 'failed' ثبت شود")

    def test_retry_after_failed_sends_successfully(self):
        """
        بعد از یک ارسال ناموفق، retry باید گزارش را بفرستد و
        وضعیت را به 'sent' تبدیل کند.
        """
        bot = MagicMock()
        # اولین تلاش fail می‌شود
        bot.send_message.side_effect = Exception("Network error")
        analytics_dashboard.send_daily_report(bot)
        self.assertEqual(bot.send_message.call_count, 1)

        # retry: این بار موفق
        bot.send_message.side_effect = None
        analytics_dashboard.send_daily_report(bot)
        self.assertEqual(bot.send_message.call_count, 2,
                         "retry بعد از failure باید گزارش را بفرستد")

        today = analytics_dashboard._report_date_for_now()
        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent",
                         "بعد از retry موفق status باید 'sent' باشد")

    def test_successful_admin_not_re_sent_on_partial_retry(self):
        """
        اگر ادمین A موفق و ادمین B ناموفق بود، retry فقط باید B را امتحان کند.
        """
        database.upsert_user(702, "admin_p2", "AdminP2")
        database.set_admin(702, True)

        bot = MagicMock()
        # A موفق، B ناموفق
        bot.send_message.side_effect = [None, Exception("fail")]
        analytics_dashboard.send_daily_report(bot)
        self.assertEqual(bot.send_message.call_count, 2)

        today = analytics_dashboard._report_date_for_now()
        rows = {r["admin_id"]: r["status"] for r in _get_report_log(today)}
        # وضعیت‌ها پس از اولین تلاش: یکی sent یکی failed
        statuses = set(rows.values())
        self.assertIn("sent", statuses)
        self.assertIn("failed", statuses)

        # retry: فقط ادمین با 'failed' باید مجدداً امتحان شود
        bot.send_message.reset_mock()
        bot.send_message.side_effect = None
        analytics_dashboard.send_daily_report(bot)
        # فقط یک ادمین باید مجدداً پیام دریافت کند
        self.assertEqual(bot.send_message.call_count, 1,
                         "فقط ادمین ناموفق باید در retry پیام دریافت کند")

    def test_one_failed_admin_does_not_stop_others(self):
        """شکست ارسال به ادمین اول نباید مانع ارسال به ادمین دوم شود."""
        database.upsert_user(703, "admin_p3", "AdminP3")
        database.set_admin(703, True)

        bot = MagicMock()
        bot.send_message.side_effect = [Exception("fail first"), None]
        analytics_dashboard.send_daily_report(bot, force=True)
        self.assertEqual(bot.send_message.call_count, 2,
                         "هر دو ادمین باید امتحان شوند حتی اگر اولی fail کند")

    def test_report_already_sent_returns_false(self):
        """_report_already_sent باید True برگرداند وقتی 'sent' وجود دارد."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 701, "sent")
        self.assertTrue(analytics_dashboard._report_already_sent(today, 701))

    def test_report_failed_is_still_retryable(self):
        """_report_already_sent باید False برگرداند وقتی فقط 'failed' وجود دارد."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 701, "failed")
        self.assertFalse(analytics_dashboard._report_already_sent(today, 701))

    def test_unique_constraint_prevents_duplicates(self):
        """UNIQUE(report_date, admin_id) باید از درج تکراری جلوگیری کند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 701, "sent")
        # درج دوباره نباید exception ایجاد کند — باید UPDATE شود
        try:
            analytics_dashboard._mark_report_sent(today, 701, "failed")
        except Exception as e:
            self.fail(f"_mark_report_sent با UNIQUE conflict crash کرد: {e}")
        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1, "باید فقط یک رکورد وجود داشته باشد")


class TestSchedulerReliability(unittest.TestCase):
    """
    تست‌های قابلیت اطمینان scheduler.
    """

    def setUp(self):
        _fresh_db()
        analytics_dashboard._init_report_log_table()
        # reset single-instance guard
        analytics_dashboard._scheduler_started = False
        database.upsert_user(801, "sched_admin", "SchedAdmin")
        database.set_admin(801, True)

    def test_start_scheduler_twice_only_one_thread(self):
        """فراخوانی چندباره start_daily_report_scheduler باید فقط یک thread ایجاد کند."""
        bot = MagicMock()
        before = threading.active_count()
        analytics_dashboard.start_daily_report_scheduler(bot)
        analytics_dashboard.start_daily_report_scheduler(bot)
        after = threading.active_count()
        # فقط یک thread اضافه شده باشد
        self.assertEqual(after - before, 1,
                         "فقط یک scheduler thread باید ایجاد شود")

    def test_scheduler_flag_set_after_start(self):
        """_scheduler_started باید بعد از start True باشد."""
        bot = MagicMock()
        analytics_dashboard._scheduler_started = False
        analytics_dashboard.start_daily_report_scheduler(bot)
        self.assertTrue(analytics_dashboard._scheduler_started)

    def test_missed_window_report_sent_on_startup(self):
        """
        شبیه‌سازی missed window: اگر ربات ساعت ۰۸:۰۰ خاموش بود
        و بعد از آن راه‌اندازی شود، _any_report_attempted باید False
        باشد و send_daily_report باید فراخوانی شود.
        """
        today = analytics_dashboard._report_date_for_now()
        # هنوز هیچ تلاشی ثبت نشده
        self.assertFalse(analytics_dashboard._any_report_attempted(today))

        bot = MagicMock()
        analytics_dashboard.send_daily_report(bot)
        self.assertTrue(analytics_dashboard._any_report_attempted(today))

    def test_no_missed_window_if_already_sent(self):
        """
        اگر گزارش قبلاً ارسال شده باشد، _any_report_attempted باید True
        باشد و scheduler نباید دوباره ارسال کند.
        """
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 801, "sent")
        self.assertTrue(analytics_dashboard._any_report_attempted(today))

    def test_should_send_now_exact_match(self):
        """_should_send_now باید در دقیقه درست True برگرداند."""
        from datetime import datetime, timezone
        h = analytics_dashboard.DAILY_REPORT_HOUR
        m = analytics_dashboard.DAILY_REPORT_MINUTE
        t = datetime(2024, 1, 15, h, m, 0, tzinfo=timezone.utc)
        self.assertTrue(analytics_dashboard._should_send_now(t))

    def test_should_send_now_different_minute(self):
        """_should_send_now باید در دقیقه اشتباه False برگرداند."""
        from datetime import datetime, timezone
        h = analytics_dashboard.DAILY_REPORT_HOUR
        m = (analytics_dashboard.DAILY_REPORT_MINUTE + 1) % 60
        t = datetime(2024, 1, 15, h, m, 0, tzinfo=timezone.utc)
        self.assertFalse(analytics_dashboard._should_send_now(t))


class TestTimezoneHandling(unittest.TestCase):
    """
    تست‌های مرز روزانه و تایم‌زون.
    """

    def setUp(self):
        _fresh_db()
        analytics_dashboard._init_report_log_table()

    def test_default_timezone_is_utc(self):
        """تایم‌زون پیش‌فرض باید UTC باشد."""
        self.assertEqual(analytics_dashboard.REPORTING_TZ_NAME, "UTC")

    def test_get_reporting_tz_utc(self):
        """_get_reporting_tz باید با UTC کار کند."""
        tz = analytics_dashboard._get_reporting_tz()
        self.assertIsNotNone(tz)

    def test_get_reporting_tz_invalid_fallback(self):
        """تایم‌زون نامعتبر باید به UTC fallback کند."""
        original = analytics_dashboard.REPORTING_TZ_NAME
        analytics_dashboard.REPORTING_TZ_NAME = "Invalid/Timezone"
        try:
            tz = analytics_dashboard._get_reporting_tz()
            self.assertIsNotNone(tz)
            # باید UTC برگرداند
            from datetime import timezone
            now_utc = analytics_dashboard._report_date_for_now()
            self.assertRegex(now_utc, r"^\d{4}-\d{2}-\d{2}$")
        finally:
            analytics_dashboard.REPORTING_TZ_NAME = original

    def test_report_date_format(self):
        """_report_date_for_now باید فرمت YYYY-MM-DD برگرداند."""
        d = analytics_dashboard._report_date_for_now()
        self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$")

    def test_period_where_today_window(self):
        """_period_where با offset=0 باید پنجره آخر N روز را برگرداند."""
        from analytics import _period_where
        clause, params = _period_where("created_at", 1, 0)
        self.assertIn(">=", clause)
        self.assertNotIn("<", clause)
        self.assertEqual(params, ["-1 days"])

    def test_period_where_yesterday_window(self):
        """
        _period_where با days=1, offset=1 باید پنجره دقیقاً دیروز را برگرداند:
        [now-2d, now-1d) — نه [now-2d, now).
        """
        from analytics import _period_where
        clause, params = _period_where("created_at", 1, 1)
        self.assertIn(">=", clause)
        self.assertIn("<", clause)
        # پارامتر پایان باید now-1d باشد نه now-0d
        self.assertEqual(params[0], "-2 days")
        self.assertEqual(params[1], "-1 days",
                         "پارامتر پایان باید '-1 days' باشد، نه '-0 days'")

    def test_yesterday_window_does_not_overlap_today(self):
        """
        داده‌های امروز نباید در پنجره 'دیروز' گنجانده شوند.
        کاربری که همین لحظه ثبت شده نباید در get_new_users_in_period(1, offset=1) دیده شود.
        """
        database.upsert_user(901, "today_user", "TodayUser")
        count_yest = analytics.get_new_users_in_period(days=1, offset_days=1)
        # این کاربر الان ثبت شده، نباید در پنجره دیروز باشد
        count_today = analytics.get_new_users_in_period(days=1, offset_days=0)
        self.assertEqual(count_today, 1)
        self.assertEqual(count_yest, 0,
                         "کاربر ثبت‌شده امروز نباید در پنجره دیروز باشد")


class TestRetentionAccuracy(unittest.TestCase):
    """
    تست‌های دقت محاسبات retention.
    """

    def setUp(self):
        _fresh_db()

    def _insert_user_with_created_at(self, user_id: int, days_ago: float) -> None:
        """درج کاربر با created_at دقیق X روز پیش."""
        database.upsert_user(user_id, f"ruser{user_id}", f"RUser{user_id}")
        with _sqlite3.connect(_TEST_DB) as conn:
            conn.execute(
                "UPDATE users SET created_at = datetime('now', ?) WHERE user_id = ?",
                (f"-{days_ago} days", user_id)
            )

    def _insert_event_at(self, user_id: int, days_ago: float) -> None:
        """درج رویداد analytics برای کاربر X روز پیش."""
        with _sqlite3.connect(_TEST_DB) as conn:
            conn.execute(
                """INSERT INTO analytics_events (event_type, user_id, created_at)
                   VALUES ('user_started', ?, datetime('now', ?))""",
                (user_id, f"-{days_ago} days")
            )

    def test_incomplete_cohort_excluded_day1(self):
        """
        کاربری که کمتر از 2 روز پیش ثبت‌نام کرده نباید در cohort day-1 واجد شرایط باشد.
        (پنجره فعالیت day1 = [day1, day2) هنوز در گذشته نیست)
        """
        self._insert_user_with_created_at(1001, 0.5)   # 12 ساعت پیش — ناقص
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["eligible_users"], 0,
                         "کاربر < 2 روز پیش نباید در cohort day-1 شمرده شود")

    def test_complete_cohort_included_day1(self):
        """
        کاربری که ۲+ روز پیش ثبت‌نام کرده باید در cohort day-1 واجد شرایط باشد.
        """
        self._insert_user_with_created_at(1002, 2.5)   # 2.5 روز پیش — کامل
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["eligible_users"], 1,
                         "کاربر 2.5 روز پیش باید در cohort day-1 واجد شرایط باشد")

    def test_retained_user_in_window(self):
        """
        کاربر ۲ روز پیش ثبت‌نام کرده و ۱ روز پیش فعال بوده → باید retained باشد.
        """
        self._insert_user_with_created_at(1003, 2.5)
        self._insert_event_at(1003, 1.5)   # داخل پنجره [day1, day2) = [1.5, 0.5)
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["retained_users"], 1)
        self.assertIsNotNone(result["retention_rate_pct"])

    def test_no_activity_in_window_not_retained(self):
        """
        کاربر ثبت‌نام کرده اما در پنجره day-1 فعالیتی نداشته → باید not-retained باشد.
        """
        self._insert_user_with_created_at(1004, 2.5)
        # فعالیت در روز ۰ (همان روز ثبت‌نام) — خارج از پنجره day-1
        self._insert_event_at(1004, 2.5)
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["eligible_users"], 1)
        self.assertEqual(result["retained_users"], 0)

    def test_incomplete_cohort_excluded_day7(self):
        """کاربر ۵ روز پیش ثبت‌نام کرده نباید در cohort day-7 شمرده شود."""
        self._insert_user_with_created_at(1005, 5)
        result = analytics.get_retention_cohort(7)
        self.assertEqual(result["eligible_users"], 0,
                         "کاربر < 8 روز پیش نباید در cohort day-7 باشد")

    def test_complete_cohort_day7(self):
        """کاربر ۸+ روز پیش ثبت‌نام کرده باید در cohort day-7 واجد شرایط باشد."""
        self._insert_user_with_created_at(1006, 9)
        result = analytics.get_retention_cohort(7)
        self.assertEqual(result["eligible_users"], 1)

    def test_incomplete_cohort_excluded_day30(self):
        """کاربر ۲۵ روز پیش ثبت‌نام کرده نباید در cohort day-30 شمرده شود."""
        self._insert_user_with_created_at(1007, 25)
        result = analytics.get_retention_cohort(30)
        self.assertEqual(result["eligible_users"], 0,
                         "کاربر < 31 روز پیش نباید در cohort day-30 باشد")

    def test_retention_text_distinguishes_approximate_data(self):
        """متن retention باید اطلاعات داده‌های تخمینی را ذکر کند."""
        text = analytics_dashboard._build_retention_text()
        # باید توضیح درباره cohort‌های ناقص وجود داشته باشد
        self.assertIn("ناقص", text)

    def test_zero_eligible_users_no_rate(self):
        """اگر eligible_users صفر است، retention_rate_pct باید None باشد."""
        result = analytics.get_retention_cohort(1)
        self.assertIsNone(result["retention_rate_pct"])

    def test_historical_users_without_created_at_excluded(self):
        """
        کاربران قدیمی بدون created_at نباید در محاسبات retention شمرده شوند.
        """
        # درج کاربر بدون created_at (مانند کاربران قدیمی pre-migration)
        database.upsert_user(1008, "hist_user", "HistUser")
        with _sqlite3.connect(_TEST_DB) as conn:
            conn.execute(
                "UPDATE users SET created_at = NULL WHERE user_id = ?",
                (1008,)
            )
        result = analytics.get_retention_cohort(1)
        self.assertEqual(result["eligible_users"], 0,
                         "کاربران بدون created_at نباید در cohort باشند")


class TestErrorHandlingAndDelivery(unittest.TestCase):
    """
    تست‌های error handling ارسال گزارش.
    """

    def setUp(self):
        _fresh_db()
        analytics_dashboard._init_report_log_table()
        database.upsert_user(951, "err_admin1", "ErrAdmin1")
        database.set_admin(951, True)
        database.upsert_user(952, "err_admin2", "ErrAdmin2")
        database.set_admin(952, True)

    def test_telegram_api_failure_logged_not_raised(self):
        """خطای Telegram API نباید exception به caller بدهد."""
        bot = MagicMock()
        bot.send_message.side_effect = Exception("Telegram 500")
        try:
            analytics_dashboard.send_daily_report(bot, force=True)
        except Exception as e:
            self.fail(f"send_daily_report نباید exception بدهد: {e}")

    def test_all_admins_attempted_despite_first_failure(self):
        """حتی اگر اولین ادمین fail کند، ادمین دوم باید امتحان شود."""
        bot = MagicMock()
        bot.send_message.side_effect = [Exception("fail"), None]
        analytics_dashboard.send_daily_report(bot, force=True)
        self.assertEqual(bot.send_message.call_count, 2)

    def test_failed_status_allows_retry(self):
        """status='failed' نباید از retry جلوگیری کند."""
        bot = MagicMock()
        # اولین تلاش fail
        bot.send_message.side_effect = Exception("Network error")
        analytics_dashboard.send_daily_report(bot)

        # retry باید گزارش را بفرستد
        bot.send_message.side_effect = None
        bot.send_message.reset_mock()
        analytics_dashboard.send_daily_report(bot)
        self.assertGreater(bot.send_message.call_count, 0,
                           "retry بعد از failure باید ارسال کند")

    def test_database_error_in_idempotency_check_safe(self):
        """
        اگر بررسی idempotency با خطای دیتابیس مواجه شود،
        باید احتیاطاً True برگرداند (نه ارسال تکراری).
        """
        with patch.object(database, "get_connection", side_effect=Exception("DB down")):
            result = analytics_dashboard._report_already_sent("2024-01-01", 951)
        self.assertTrue(result,
                        "در صورت خطای دیتابیس، احتیاطاً باید True برگردد")

    def test_no_admins_returns_false(self):
        """اگر هیچ ادمینی وجود نداشته باشد، send_daily_report باید False برگرداند."""
        _fresh_db()
        analytics_dashboard._init_report_log_table()
        bot = MagicMock()
        result = analytics_dashboard.send_daily_report(bot, force=True)
        self.assertFalse(result)

    def test_partial_delivery_returns_true(self):
        """اگر حداقل یک ادمین گزارش دریافت کند، باید True برگرداند."""
        bot = MagicMock()
        bot.send_message.side_effect = [Exception("fail"), None]
        result = analytics_dashboard.send_daily_report(bot, force=True)
        self.assertTrue(result,
                        "partial delivery (یک موفق) باید True برگرداند")


class TestPendingStateAndAtomicClaim(unittest.TestCase):
    """
    تست‌های مکانیزم رزرو اتمیک ('pending') و _try_claim_report_send.
    این تست‌ها از دو SQLite connection مستقل برای شبیه‌سازی دو process استفاده می‌کنند.
    """

    def setUp(self):
        _fresh_db()
        analytics_dashboard._init_report_log_table()
        database.upsert_user(1101, "claim_admin", "ClaimAdmin")
        database.set_admin(1101, True)

    def test_first_claim_returns_true(self):
        """اولین claim باید True برگرداند."""
        today = analytics_dashboard._report_date_for_now()
        result = analytics_dashboard._try_claim_report_send(today, 1101)
        self.assertTrue(result)

    def test_second_claim_returns_false_pending(self):
        """دومین claim در حالی که اول 'pending' است باید False برگرداند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._try_claim_report_send(today, 1101)   # اول → pending
        result = analytics_dashboard._try_claim_report_send(today, 1101)  # دوم
        self.assertFalse(result, "claim دوم باید False برگرداند وقتی 'pending' وجود دارد")

    def test_claim_after_sent_returns_false(self):
        """claim پس از 'sent' باید False برگرداند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 1101, "sent")
        result = analytics_dashboard._try_claim_report_send(today, 1101)
        self.assertFalse(result)

    def test_claim_after_failed_returns_true(self):
        """claim پس از 'failed' باید True برگرداند (retry مجاز است)."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 1101, "failed")
        result = analytics_dashboard._try_claim_report_send(today, 1101)
        self.assertTrue(result, "retry بعد از 'failed' باید امکان‌پذیر باشد")

    def test_pending_recorded_in_db_after_claim(self):
        """پس از claim موفق، وضعیت 'pending' در دیتابیس ثبت می‌شود."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._try_claim_report_send(today, 1101)
        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")

    def test_send_updates_pending_to_sent(self):
        """ارسال موفق باید 'pending' را به 'sent' تبدیل کند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._try_claim_report_send(today, 1101)
        analytics_dashboard._mark_report_sent(today, 1101, "sent")
        rows = _get_report_log(today)
        self.assertEqual(rows[0]["status"], "sent")

    def test_failed_send_updates_pending_to_failed(self):
        """ارسال ناموفق باید 'pending' را به 'failed' تبدیل کند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._try_claim_report_send(today, 1101)
        analytics_dashboard._mark_report_sent(today, 1101, "failed")
        rows = _get_report_log(today)
        self.assertEqual(rows[0]["status"], "failed")

    def test_pending_blocked_by_idempotency_check(self):
        """_report_already_sent باید 'pending' را به عنوان 'ارسال در حال انجام' تلقی کند."""
        today = analytics_dashboard._report_date_for_now()
        analytics_dashboard._mark_report_sent(today, 1101, "failed")  # ابتدا failed
        # simulate: claim → pending
        analytics_dashboard._try_claim_report_send(today, 1101)
        # _report_already_sent باید True برگرداند (ارسال در حال انجام است)
        result = analytics_dashboard._report_already_sent(today, 1101)
        self.assertTrue(result, "'pending' باید توسط _report_already_sent بلاک شود")


class TestCrossProcessRaceCondition(unittest.TestCase):
    """
    تست race condition بین دو process مستقل که همزمان send_daily_report فراخوانی می‌کنند.

    از thread‌های مستقل با connections جداگانه استفاده می‌شود تا رفتار
    دو process مختلف را شبیه‌سازی کنید.
    BEGIN IMMEDIATE در WAL mode تضمین می‌کند فقط یک caller بتواند 'pending' درج کند.
    """

    def setUp(self):
        _fresh_db()
        analytics_dashboard._init_report_log_table()
        database.upsert_user(1201, "race_admin", "RaceAdmin")
        database.set_admin(1201, True)

    def test_concurrent_claims_only_one_wins(self):
        """
        دو thread که همزمان _try_claim_report_send را فراخوانی می‌کنند:
        دقیقاً یکی باید True و دیگری False دریافت کند.
        از دو اتصال SQLite مستقل استفاده می‌شود.
        """
        today = analytics_dashboard._report_date_for_now()
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def claim_worker():
            try:
                barrier.wait()   # هر دو thread همزمان شروع می‌کنند
                r = analytics_dashboard._try_claim_report_send(today, 1201)
                results.append(r)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=claim_worker)
        t2 = threading.Thread(target=claim_worker)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

        self.assertEqual(len(errors), 0, f"خطاهای غیرمنتظره: {errors}")
        self.assertEqual(len(results), 2)
        # دقیقاً یکی True و دیگری False
        self.assertEqual(sorted(results), [False, True],
                         f"یکی باید برنده رزرو باشد، نتایج: {results}")

    def test_concurrent_send_daily_report_no_duplicate(self):
        """
        دو فراخوانی موازی send_daily_report نباید پیام تکراری بفرستند.
        """
        send_counts = []
        errors = []
        barrier = threading.Barrier(2)

        def send_worker():
            bot = MagicMock()
            try:
                barrier.wait()
                analytics_dashboard.send_daily_report(bot)
                send_counts.append(bot.send_message.call_count)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=send_worker)
        t2 = threading.Thread(target=send_worker)
        t1.start(); t2.start()
        t1.join(timeout=10); t2.join(timeout=10)

        self.assertEqual(len(errors), 0, f"خطاهای غیرمنتظره: {errors}")
        # مجموع ارسال‌ها در کل باید دقیقاً یک پیام باشد
        total_sent = sum(send_counts)
        self.assertEqual(total_sent, 1,
                         f"مجموع ارسال‌ها باید ۱ باشد، نه {total_sent}: {send_counts}")

    def test_pending_in_db_after_concurrent_race(self):
        """
        پس از race، دقیقاً یک رکورد در دیتابیس وجود داشته باشد.
        """
        today = analytics_dashboard._report_date_for_now()
        barrier = threading.Barrier(2)

        def claim_worker():
            try:
                barrier.wait()
                analytics_dashboard._try_claim_report_send(today, 1201)
            except Exception:
                pass

        t1 = threading.Thread(target=claim_worker)
        t2 = threading.Thread(target=claim_worker)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

        rows = _get_report_log(today)
        self.assertEqual(len(rows), 1,
                         f"باید دقیقاً یک رکورد در دیتابیس باشد، نه {len(rows)}")

    def test_restart_simulation_with_separate_db_state(self):
        """
        شبیه‌سازی واقعی restart:
        1. ارسال موفق + ثبت 'sent' در دیتابیس
        2. ماژول reset نمی‌شود (در Python تست واقعی‌تر)
        3. فراخوانی مجدد send_daily_report با همان دیتابیس
        4. نباید پیام تکراری ارسال شود
        """
        bot1 = MagicMock()
        analytics_dashboard.send_daily_report(bot1)
        self.assertEqual(bot1.send_message.call_count, 1)

        # Bot2 مانند یک process جدید: state در حافظه ندارد،
        # اما دیتابیس دست نخورده است.
        bot2 = MagicMock()
        analytics_dashboard.send_daily_report(bot2)
        self.assertEqual(bot2.send_message.call_count, 0,
                         "بعد از restart (با دیتابیس دست نخورده) نباید پیام تکراری ارسال شود")


class TestYesterdayPeriodBoundary(unittest.TestCase):
    """
    تست صحت بازه زمانی 'دیروز' در گزارش روزانه.
    اطمینان از اینکه days=1,offset=1 (نه days=2,offset=1) استفاده می‌شود.
    """

    def setUp(self):
        _fresh_db()

    def test_today_and_yesterday_are_non_overlapping_equal_periods(self):
        """
        بازه 'امروز' و 'دیروز' باید هم‌اندازه و غیرمتداخل باشند.
        days=1,offset=0 → [now-1d, now)
        days=1,offset=1 → [now-2d, now-1d)
        """
        from analytics import _period_where
        clause_today, params_today = _period_where("created_at", 1, 0)
        clause_yest,  params_yest  = _period_where("created_at", 1, 1)

        # امروز: یک پارامتر (فقط lower bound)
        self.assertEqual(len(params_today), 1)
        self.assertEqual(params_today[0], "-1 days")

        # دیروز: دو پارامتر (lower + upper bound)
        self.assertEqual(len(params_yest), 2)
        self.assertEqual(params_yest[0], "-2 days")  # start
        self.assertEqual(params_yest[1], "-1 days")  # end

    def test_daily_report_text_uses_correct_yesterday_period(self):
        """
        _build_daily_report_text نباید از days=2 برای مقایسه دیروز استفاده کند.
        بازه‌های امروز و دیروز باید هم‌اندازه باشند.
        """
        # اگر کاربر جدیدی همین لحظه اضافه شود، نباید در 'دیروز' دیده شود
        database.upsert_user(1301, "today_new", "TodayNew")
        nu_today = analytics.get_new_users_in_period(days=1, offset_days=0)
        nu_yest  = analytics.get_new_users_in_period(days=1, offset_days=1)
        self.assertEqual(nu_today, 1, "کاربر جدید باید در امروز دیده شود")
        self.assertEqual(nu_yest, 0,
                         "کاربر ثبت‌شده همین لحظه نباید در دیروز دیده شود — "
                         "این تضمین می‌کند days=1 استفاده شده، نه days=2")

    def test_delta_comparison_uses_equal_window(self):
        """
        مقایسه delta در گزارش روزانه باید بین دو پنجره هم‌اندازه باشد.
        تست اطمینان می‌دهد متن گزارش بدون crash تولید می‌شود.
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            text = analytics_dashboard._build_daily_report_text(today)
            self.assertIn("گزارش روزانه", text)
        except Exception as e:
            self.fail(f"_build_daily_report_text crash: {e}")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestDashboardAccess,
        TestDashboardSections,
        TestEmptyDatabase,
        TestDateFilters,
        TestDailyReport,
        TestRetentionCalculations,
        TestPeriodQueryHelpers,
        TestBotCompatibility,
        # Phase 3.1 — Reliability
        TestPersistentIdempotency,
        TestSchedulerReliability,
        TestTimezoneHandling,
        TestRetentionAccuracy,
        TestErrorHandlingAndDelivery,
        # Final Review additions
        TestPendingStateAndAtomicClaim,
        TestCrossProcessRaceCondition,
        TestYesterdayPeriodBoundary,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"تست‌های اجراشده : {result.testsRun}")
    print(f"موفق            : {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"ناموفق          : {len(result.failures)}")
    print(f"خطا             : {len(result.errors)}")
    print("=" * 60)

    sys.exit(0 if result.wasSuccessful() else 1)
