"""
analytics_dashboard.py — داشبورد Analytics در Telegram

این ماژول رابط کاربری Telegram برای داشبورد analytics را پیاده‌سازی می‌کند.
تمام منطق محاسبات از analytics.py خوانده می‌شود.
تمام callback‌ها با prefix "adm_anl:" هستند تا با سایر callback‌های ادمین تداخل نداشته باشند.

گزارش روزانه خودکار:
  - ساعت قابل تنظیم (پیش‌فرض: ۰۸:۰۰ UTC)
  - Idempotency پایدار از طریق جدول daily_report_log در SQLite
  - Delivery status به ازای هر ادمین ردیابی می‌شود
  - Restart ایمن: restart نمی‌تواند گزارش تکراری ایجاد کند
  - با daemon thread ایمن
  - Missed-window recovery: اگر ربات در پنجره ۰۸:۰۰ خاموش بود،
    اولین بار که بعد از آن ساعت راه‌اندازی می‌شود گزارش ارسال می‌کند

TimeZone:
  - پیش‌فرض: UTC
  - قابل تغییر از طریق REPORTING_TZ (نام IANA مانند 'Asia/Tehran')
  - تمام timestamps در دیتابیس UTC هستند
  - مرز روزانه برای گزارش‌دهی بر اساس REPORTING_TZ محاسبه می‌شود
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from telebot import types
import database
import analytics

logger = logging.getLogger(__name__)

# ساعت ارسال گزارش روزانه (در REPORTING_TZ)
DAILY_REPORT_HOUR: int = 8
DAILY_REPORT_MINUTE: int = 0

# تایم‌زون گزارش‌دهی — نام IANA یا 'UTC'
# برای تغییر: REPORTING_TZ = 'Asia/Tehran'
# پیش‌فرض UTC: تمام مرزهای روزانه UTC هستند
REPORTING_TZ_NAME: str = "UTC"

# ── Callback prefix 
PREFIX = "adm_anl"

# ── Scheduler guard: just one instance scheduler 
_scheduler_started = False
_scheduler_lock = threading.Lock()

# ── Idempotency lock
_daily_report_lock = threading.Lock()



# part0: Persistent idempotency — daily_report_log table

def _init_report_log_table() -> None:

    try:
        with database.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_report_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_date TEXT    NOT NULL,
                    admin_id    INTEGER NOT NULL,
                    status      TEXT    NOT NULL CHECK(status IN ('sent','failed','pending')),
                    sent_at     TEXT    NOT NULL,
                    UNIQUE(report_date, admin_id)
                )
            """)
            # Migration: اگر جدول قدیمی بدون 'pending' وجود دارد، آن را بازسازی کن.
            # CREATE TABLE IF NOT EXISTS جدول موجود را تغییر نمی‌دهد، پس
            # با یک INSERT آزمایشی بررسی می‌کنیم آیا 'pending' مجاز است.
            try:
                conn.execute(
                    "INSERT INTO daily_report_log "
                    "(report_date, admin_id, status, sent_at) "
                    "VALUES ('__schema_check__', 0, 'pending', datetime('now'))"
                )
                conn.execute(
                    "DELETE FROM daily_report_log "
                    "WHERE report_date='__schema_check__'"
                )
            except Exception:
                # جدول قدیمی 'pending' را نمی‌پذیرد → بازسازی با حفظ داده
                logger.info(
                    "[dashboard] Migrating daily_report_log to support 'pending' status."
                )
                conn.execute(
                    "ALTER TABLE daily_report_log RENAME TO daily_report_log_v1"
                )
                conn.execute("""
                    CREATE TABLE daily_report_log (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        report_date TEXT    NOT NULL,
                        admin_id    INTEGER NOT NULL,
                        status      TEXT    NOT NULL
                            CHECK(status IN ('sent','failed','pending')),
                        sent_at     TEXT    NOT NULL,
                        UNIQUE(report_date, admin_id)
                    )
                """)
                conn.execute("""
                    INSERT INTO daily_report_log
                        (id, report_date, admin_id, status, sent_at)
                    SELECT id, report_date, admin_id, status, sent_at
                    FROM daily_report_log_v1
                """)
                conn.execute("DROP TABLE daily_report_log_v1")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_report_log_date "
                "ON daily_report_log(report_date)"
            )
            conn.commit()
    except Exception as exc:
        logger.error("[dashboard] _init_report_log_table failed: %s", exc)


def _get_reporting_tz():
    """
    برگرداندن شیء timezone برای REPORTING_TZ_NAME.
    در صورت خطا UTC برمی‌گردد.
    """
    if REPORTING_TZ_NAME == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo          # Python 3.9+
        return ZoneInfo(REPORTING_TZ_NAME)
    except ImportError:
        try:
            import pytz
            return pytz.timezone(REPORTING_TZ_NAME)
        except Exception:
            pass
    except Exception:
        pass
    logger.warning("[dashboard] Unknown timezone '%s', falling back to UTC.", REPORTING_TZ_NAME)
    return timezone.utc


def _report_date_for_now() -> str:
    """تاریخ گزارش روز جاری در REPORTING_TZ به فرمت 'YYYY-MM-DD'."""
    tz = _get_reporting_tz()
    return datetime.now(tz).strftime("%Y-%m-%d")


def _report_already_sent(report_date: str, admin_id: int) -> bool:

    try:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM daily_report_log "
                "WHERE report_date=? AND admin_id=? AND status IN ('sent','pending')",
                (report_date, admin_id),
            ).fetchone()
        return row is not None
    except Exception as exc:
        logger.error("[dashboard] _report_already_sent check failed: %s", exc)
        # در صورت خطای دیتابیس، احتیاطاً True برمی‌گردیم تا از ارسال تکراری
        # جلوگیری شود — بهتر از ارسال تکراری است.
        return True


def _try_claim_report_send(report_date: str, admin_id: int) -> bool:

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with database.get_connection() as conn:
            # autocommit غیرفعال — تراکنش دستی
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status FROM daily_report_log "
                    "WHERE report_date=? AND admin_id=?",
                    (report_date, admin_id),
                ).fetchone()

                if row is not None:
                    status = row["status"]
                    if status in ("sent", "pending"):
                        # قبلاً ارسال شده یا process دیگری در حال ارسال است
                        conn.execute("ROLLBACK")
                        return False
                    # status == 'failed': retry مجاز — ارتقا به 'pending'
                    conn.execute(
                        "UPDATE daily_report_log SET status='pending', sent_at=? "
                        "WHERE report_date=? AND admin_id=?",
                        (now_iso, report_date, admin_id),
                    )
                else:
                    # اولین تلاش: درج با وضعیت 'pending'
                    conn.execute(
                        "INSERT INTO daily_report_log "
                        "(report_date, admin_id, status, sent_at) "
                        "VALUES (?, ?, 'pending', ?)",
                        (report_date, admin_id, now_iso),
                    )
                conn.execute("COMMIT")
                return True
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
    except Exception as exc:
        logger.error(
            "[dashboard] _try_claim_report_send failed (date=%s admin=%s): %s",
            report_date, admin_id, exc,
        )
        # در صورت خطای دیتابیس: احتیاطاً ارسال نکن
        return False


def _mark_report_sent(report_date: str, admin_id: int, status: str) -> None:
    """
    ثبت وضعیت delivery گزارش در daily_report_log.
    از INSERT OR REPLACE استفاده می‌کند تا retry بتواند 'failed' را به 'sent' تبدیل کند.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with database.get_connection() as conn:
            conn.execute(
                """INSERT INTO daily_report_log (report_date, admin_id, status, sent_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(report_date, admin_id)
                   DO UPDATE SET status=excluded.status, sent_at=excluded.sent_at
                """,
                (report_date, admin_id, status, now_iso),
            )
            conn.commit()
    except Exception as exc:
        logger.error(
            "[dashboard] _mark_report_sent failed (date=%s admin=%s status=%s): %s",
            report_date, admin_id, status, exc,
        )


def _any_report_attempted(report_date: str) -> bool:
    try:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM daily_report_log WHERE report_date=? LIMIT 1",
                (report_date,),
            ).fetchone()
        return row is not None
    except Exception as exc:
        logger.error("[dashboard] _any_report_attempted check failed: %s", exc)
        return False


# part1:text makers

def _pct(value: Optional[float], suffix: str = "%") -> str:
    """فرمت درصد با مقدار پیش‌فرض برای None."""
    if value is None:
        return "—"
    return f"{value:.1f}{suffix}"


def _n(value) -> str:
    """فرمت عدد با separator هزارتایی."""
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _delta(current: int, previous: int) -> str:
    """نمایش تفاوت نسبت به دیروز/دوره قبل."""
    if previous == 0:
        return ""
    diff = current - previous
    if diff > 0:
        return f" ↑{diff:,}"
    elif diff < 0:
        return f" ↓{abs(diff):,}"
    return " ═"


def _period_label(period_key: str) -> str:
    """برچسب فارسی برای هر بازه زمانی."""
    labels = {
        "today":   "امروز / Today",
        "week":    "۷ روز اخیر / Last 7 days",
        "month":   "۳۰ روز اخیر / Last 30 days",
        "quarter": "۹۰ روز اخیر / Last 90 days",
        "all":     "کل تاریخ / All time",
    }
    return labels.get(period_key, period_key)


def _safe_div(a: int, b: int) -> float:
    return round(a / b * 100, 1) if b else 0.0


# Brief Report:

def _build_overview_text() -> str:
    """متن بخش خلاصه کلی."""
    u  = analytics.get_user_stats()
    au_today = analytics.get_active_users_in_period(days=1)
    au_week  = analytics.get_active_users_in_period(days=7)
    au_month = analytics.get_active_users_in_period(days=30)
    nu_today = analytics.get_new_users_in_period(days=1)
    nu_week  = analytics.get_new_users_in_period(days=7)
    nu_month = analytics.get_new_users_in_period(days=30)
    s  = analytics.get_search_stats()
    d  = analytics.get_delivery_stats()

    lines = [
        "📊 <b>داشبورد Analytics — خلاصه کلی / Overview</b>\n",
        "👥 <b>کاربران / Users</b>",
        f"  کل ثبت‌نام‌شده / Total registered:  <b>{_n(u.get('total_users'))}</b>",
        f"  جدید امروز / New today:              <b>{_n(nu_today)}</b>",
        f"  جدید ۷ روز / New (7d):               <b>{_n(nu_week)}</b>",
        f"  جدید ۳۰ روز / New (30d):             <b>{_n(nu_month)}</b>",
        f"  فعال امروز / Active today:           <b>{_n(au_today)}</b>",
        f"  فعال ۷ روز / Active (7d):            <b>{_n(au_week)}</b>",
        f"  فعال ۳۰ روز / Active (30d):          <b>{_n(au_month)}</b>",
        "",
        "🔍 <b>جستجو / Search</b>",
        f"  کل جستجوها / Total searches:         <b>{_n(s.get('total_submitted'))}</b>",
        f"  نرخ موفقیت / Success rate:           <b>{_pct(s.get('conversion_rate_pct'))}</b>",
        "",
        "📥 <b>تحویل فایل / File Delivery</b>",
        f"  کل درخواست‌ها / Total requests:      <b>{_n(d.get('total_requested'))}</b>",
        f"  نرخ موفقیت / Success rate:           <b>{_pct(d.get('success_rate_pct'))}</b>",
    ]
    return "\n".join(lines)


# User Statistics

def _build_user_stats_text(period_key: str = "all") -> str:
    u = analytics.get_user_stats()
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    days = days_map.get(period_key)

    nu = analytics.get_new_users_in_period(days=days) if days else u.get("total_users", 0)
    au = analytics.get_active_users_in_period(days=days) if days else u.get("total_users", 0)

    total       = u.get("total_users", 0)
    searched    = u.get("users_searched_at_least_once", 0)
    never       = u.get("users_never_searched", 0)
    with_hist   = u.get("users_with_created_at", 0)
    conv        = _safe_div(searched, total)

    lines = [
        f"👥 <b>آمار کاربران / User Stats — {_period_label(period_key)}</b>\n",
        f"کل کاربران ثبت‌شده / Total registered:   <b>{_n(total)}</b>",
        f"کاربران جدید / New users:                <b>{_n(nu)}</b>",
        f"کاربران فعال / Active users:             <b>{_n(au)}</b>",
        "",
        "📊 <b>رفتار جستجو / Search Behaviour</b> <i>(از راه‌اندازی analytics / since analytics launch)</i>",
        f"حداقل یک جستجو / At least one search:   <b>{_n(searched)}</b>",
        f"هرگز جستجو نکرده / Never searched:       <b>{_n(never)}</b>",
        f"نرخ تبدیل جستجو / Search conversion:    <b>{_pct(conv)}</b>",
        "",
        f"<i>⚠️ نرخ تبدیل / Conversion rate = users with ≥1 logged search ÷ total users.</i>",
        f"<i>کاربران قدیمی‌تر از راه‌اندازی analytics به صورت خودکار «جستجو‌نکرده» محسوب نمی‌شوند / Users predating analytics are not auto-counted as non-searchers.</i>",
        f"<i>کاربران با تاریخ ثبت‌نام معتبر / Users with valid registration date: {_n(with_hist)}</i>",
    ]
    return "\n".join(lines)


# Search Stats:

def _build_search_stats_text(period_key: str = "all") -> str:
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    days = days_map.get(period_key)
    s = analytics.get_search_stats_in_period(days=days)

    submitted   = s.get("total_submitted", 0)
    completed   = s.get("total_completed", 0)
    no_results  = s.get("total_no_results", 0)
    errors      = s.get("total_errors", 0)
    unique      = s.get("unique_searchers", 0)
    avg         = round(submitted / unique, 1) if unique else 0

    lines = [
        f"🔍 <b>آمار جستجو / Search Stats — {_period_label(period_key)}</b>\n",
        f"کل جستجوهای ارسال‌شده / Total submitted:    <b>{_n(submitted)}</b>",
        f"جستجوهای موفق / Successful (with results):  <b>{_n(completed)}</b>",
        f"جستجو بدون نتیجه / No results:              <b>{_n(no_results)}</b>",
        f"خطای فنی جستجو / Technical errors:          <b>{_n(errors)}</b>",
        f"کاربران یکتای جستجوکننده / Unique searchers:<b>{_n(unique)}</b>",
        f"میانگین جستجو per کاربر / Avg per user:     <b>{avg}</b>",
        f"نرخ موفقیت جستجو / Search success rate:     <b>{_pct(s.get('conversion_rate_pct'))}</b>",
    ]
    return "\n".join(lines)


# ── آمار تحویل فایل 

def _build_delivery_stats_text(period_key: str = "all") -> str:
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    days = days_map.get(period_key)
    d = analytics.get_delivery_stats_in_period(days=days)

    lines = [
        f"📥 <b>آمار تحویل فایل / File Delivery Stats — {_period_label(period_key)}</b>\n",
        f"کل درخواست‌های دانلود / Total download requests: <b>{_n(d.get('total_requested'))}</b>",
        f"تحویل موفق / Successful deliveries:              <b>{_n(d.get('total_sent'))}</b>",
        f"تحویل ناموفق / Failed deliveries:               <b>{_n(d.get('total_failed'))}</b>",
        f"نرخ موفقیت تحویل / Delivery success rate:       <b>{_pct(d.get('success_rate_pct'))}</b>",
    ]
    return "\n".join(lines)


# ── Retention 

def _build_retention_text() -> str:
    r1  = analytics.get_retention_cohort(1)
    r7  = analytics.get_retention_cohort(7)
    r30 = analytics.get_retention_cohort(30)

    def _fmt_cohort(r: dict, label: str) -> str:
        eligible = r.get("eligible_users", 0)
        retained = r.get("retained_users", 0)
        rate     = r.get("retention_rate_pct")
        note     = r.get("note", "")
        if eligible == 0:
            return f"{label}: <i>داده کافی وجود ندارد / Insufficient data</i>"
        return (
            f"{label}: <b>{_pct(rate)}</b>"
            f"  <i>({_n(retained)} از {_n(eligible)})</i>"
        )

    lines = [
        "🔄 <b>Retention کاربران / User Retention</b>\n",
        _fmt_cohort(r1,  "روز ۱  (Day 1) "),
        _fmt_cohort(r7,  "روز ۷  (Day 7) "),
        _fmt_cohort(r30, "روز ۳۰ (Day 30)"),
        "",
        "<i>تعریف / Definition: کاربری retained محسوب می‌شود که در روز X ± ۱۲ ساعت پس از ثبت‌نام، حداقل یک رویداد داشته باشد / A user is retained if they had at least one event within day X ± 12 hours of registration.</i>",
        "<i>cohort‌های ناقص حذف می‌شوند / Incomplete cohorts (less than X days elapsed) are excluded from the final calculation.</i>",
        f"<i>فقط کاربران ثبت‌شده بعد از راه‌اندازی analytics واجد شرایط هستند / Only users registered after analytics launch are eligible.</i>",
    ]
    return "\n".join(lines)


# ── growth

def _build_growth_text(period: str = "day") -> str:
    period_labels = {"day": "روزانه / Daily", "week": "هفتگی / Weekly", "month": "ماهانه / Monthly"}
    n_map = {"day": 14, "week": 8, "month": 6}
    n = n_map.get(period, 14)

    rows = analytics.get_new_users(period=period, n=n)
    act  = analytics.get_active_users(period=period, n=n)

    # ایجاد dict برای lookup سریع
    act_map = {r["period"]: r["active_users"] for r in act}

    label = period_labels.get(period, period)
    lines = [f"📈 <b>رشد / Growth — نمای {label}</b>\n"]

    if not rows:
        lines.append("<i>داده کافی برای نمایش وجود ندارد / Not enough data to display.</i>")
        return "\n".join(lines)

    for r in rows[-10:]:      # حداکثر ۱۰ ردیف آخر
        p     = r["period"]
        new_u = r["new_users"]
        act_u = act_map.get(p, 0)
        lines.append(f"<code>{p}</code>  جدید/New: <b>{_n(new_u)}</b>  فعال/Active: <b>{_n(act_u)}</b>")

    return "\n".join(lines)


# ── Daily Report

def _build_daily_report_text(report_date: str) -> str:
    """
    ساخت متن گزارش روزانه برای یک تاریخ مشخص.
    report_date: 'YYYY-MM-DD' UTC
    """
    # days=1, offset_days=0  → [now-1d, now)      = امروز (۲۴ ساعت اخیر)
    # days=1, offset_days=1  → [now-2d, now-1d)   = دیروز (۲۴ ساعت معادل)
    # استفاده از days=2 با offset=1 اشتباه بود: پنجره ۴۸ ساعته می‌داد،
    # که قابل مقایسه با پنجره ۲۴ ساعته امروز نیست.
    today_s = analytics.get_search_stats_in_period(days=1)
    yest_s  = analytics.get_search_stats_in_period(days=1, offset_days=1)
    today_d = analytics.get_delivery_stats_in_period(days=1)
    yest_d  = analytics.get_delivery_stats_in_period(days=1, offset_days=1)

    nu_today = analytics.get_new_users_in_period(days=1)
    nu_yest  = analytics.get_new_users_in_period(days=1, offset_days=1)
    au_today = analytics.get_active_users_in_period(days=1)
    au_yest  = analytics.get_active_users_in_period(days=1, offset_days=1)

    t_sub  = today_s.get("total_submitted", 0)
    y_sub  = yest_s.get("total_submitted", 0)
    t_comp = today_s.get("total_completed", 0)
    y_comp = yest_s.get("total_completed", 0)
    t_no   = today_s.get("total_no_results", 0)
    t_err  = today_s.get("total_errors", 0)
    t_uniq = today_s.get("unique_searchers", 0)
    t_sent = today_d.get("total_sent", 0)
    y_sent = yest_d.get("total_sent", 0)
    t_fail = today_d.get("total_failed", 0)

    lines = [
        f"📋 <b>گزارش روزانه / Daily Report — {report_date} UTC</b>\n",
        "👥 <b>کاربران / Users</b>",
        f"  جدید / New:          <b>{_n(nu_today)}</b>{_delta(nu_today, nu_yest)}",
        f"  فعال / Active:       <b>{_n(au_today)}</b>{_delta(au_today, au_yest)}",
        "",
        "🔍 <b>جستجو / Search</b>",
        f"  کل جستجو / Total:          <b>{_n(t_sub)}</b>{_delta(t_sub, y_sub)}",
        f"  جستجوکنندگان / Searchers:  <b>{_n(t_uniq)}</b>",
        f"  با نتیجه / With results:   <b>{_n(t_comp)}</b>{_delta(t_comp, y_comp)}",
        f"  بدون نتیجه / No results:   <b>{_n(t_no)}</b>",
        f"  خطای فنی / Errors:         <b>{_n(t_err)}</b>",
        "",
        "📥 <b>تحویل فایل / File Delivery</b>",
        f"  تحویل موفق / Successful:   <b>{_n(t_sent)}</b>{_delta(t_sent, y_sent)}",
        f"  تحویل ناموفق / Failed:     <b>{_n(t_fail)}</b>",
        "",
        f"<i>↑↓ تفاوت نسبت به دیروز / vs yesterday | ═ بدون تغییر / no change</i>",
    ]
    return "\n".join(lines)


# part2: Keyboard سازنده‌ها


def analytics_main_keyboard() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("🔭 خلاصه کلی / Overview",    callback_data=f"{PREFIX}:overview"),
        types.InlineKeyboardButton("👥 کاربران / Users",          callback_data=f"{PREFIX}:users:all"),
    )
    mk.add(
        types.InlineKeyboardButton("🔍 جستجو / Search",           callback_data=f"{PREFIX}:search:all"),
        types.InlineKeyboardButton("📥 تحویل / Delivery",         callback_data=f"{PREFIX}:delivery:all"),
    )
    mk.add(
        types.InlineKeyboardButton("🔄 Retention",                callback_data=f"{PREFIX}:retention"),
        types.InlineKeyboardButton("📈 رشد / Growth",             callback_data=f"{PREFIX}:growth:day"),
    )
    mk.add(
        types.InlineKeyboardButton("🔙 بازگشت / Back to Panel",   callback_data="adm_open_panel"),
    )
    return mk


def period_filter_keyboard(section: str, current: str = "all") -> types.InlineKeyboardMarkup:
    """کیبورد انتخاب بازه زمانی برای هر بخش."""
    mk = types.InlineKeyboardMarkup(row_width=3)
    periods = [
        ("today",   "امروز / Today"),
        ("week",    "۷ روز / 7d"),
        ("month",   "۳۰ روز / 30d"),
        ("quarter", "۹۰ روز / 90d"),
        ("all",     "همه / All"),
    ]
    buttons = []
    for key, label in periods:
        mark = "●" if key == current else ""
        buttons.append(
            types.InlineKeyboardButton(
                f"{mark}{label}",
                callback_data=f"{PREFIX}:{section}:{key}"
            )
        )
    mk.add(*buttons)
    mk.add(
        types.InlineKeyboardButton("🔄 بروزرسانی / Refresh", callback_data=f"{PREFIX}:{section}:{current}"),
        types.InlineKeyboardButton("🔙 داشبورد / Dashboard", callback_data=f"{PREFIX}:main"),
    )
    return mk


def growth_period_keyboard(current: str = "day") -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=3)
    periods = [("day", "روزانه / Daily"), ("week", "هفتگی / Weekly"), ("month", "ماهانه / Monthly")]
    buttons = []
    for key, label in periods:
        mark = "●" if key == current else ""
        buttons.append(
            types.InlineKeyboardButton(
                f"{mark}{label}",
                callback_data=f"{PREFIX}:growth:{key}"
            )
        )
    mk.add(*buttons)
    mk.add(
        types.InlineKeyboardButton("🔄 بروزرسانی / Refresh", callback_data=f"{PREFIX}:growth:{current}"),
        types.InlineKeyboardButton("🔙 داشبورد / Dashboard", callback_data=f"{PREFIX}:main"),
    )
    return mk


def back_to_main_keyboard() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("🔄 بروزرسانی / Refresh", callback_data=f"{PREFIX}:overview"),
        types.InlineKeyboardButton("🔙 داشبورد / Dashboard", callback_data=f"{PREFIX}:main"),
    )
    return mk


# ═══════════════════════════════════════════════════════════════════════════════
# بخش سوم: Entry points (فراخوانی از admin.py)
# ═══════════════════════════════════════════════════════════════════════════════

def show_analytics_main(bot, chat_id: int):
    """نمایش منوی اصلی داشبورد analytics."""
    bot.send_message(
        chat_id,
        "📊 <b>داشبورد Analytics / Analytics Dashboard</b>\nیک بخش را انتخاب کنید / Select a section:",
        reply_markup=analytics_main_keyboard(),
        parse_mode="HTML",
    )


def handle_analytics_callback(bot, callback: types.CallbackQuery) -> bool:
    """
    مدیریت تمام callback‌های analytics.
    فقط برای callback‌های با prefix 'adm_anl:' فراخوانی می‌شود.
    Returns True اگر callback مدیریت شد.

    ⚠️ تأیید is_admin باید در caller (admin.py) انجام شده باشد.
    """
    uid  = callback.from_user.id
    data = callback.data  # e.g. "adm_anl:overview"

    if not data.startswith(f"{PREFIX}:"):
        return False

    parts = data[len(PREFIX) + 1:].split(":")
    section = parts[0] if parts else ""

    try:
        bot.answer_callback_query(callback.id)

        if section == "main":
            _edit_or_send(bot, callback, "📊 <b>داشبورد Analytics / Analytics Dashboard</b>\nیک بخش را انتخاب کنید / Select a section:",
                          analytics_main_keyboard())

        elif section == "overview":
            text = _build_overview_text()
            _edit_or_send(bot, callback, text, back_to_main_keyboard())

        elif section == "users":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_user_stats_text(period)
            _edit_or_send(bot, callback, text, period_filter_keyboard("users", period))

        elif section == "search":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_search_stats_text(period)
            _edit_or_send(bot, callback, text, period_filter_keyboard("search", period))

        elif section == "delivery":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_delivery_stats_text(period)
            _edit_or_send(bot, callback, text, period_filter_keyboard("delivery", period))

        elif section == "retention":
            text = _build_retention_text()
            _edit_or_send(bot, callback, text, back_to_main_keyboard())

        elif section == "growth":
            period = parts[1] if len(parts) > 1 else "day"
            text   = _build_growth_text(period)
            _edit_or_send(bot, callback, text, growth_period_keyboard(period))

        else:
            return False

        return True

    except Exception as exc:
        logger.error("[dashboard] handle_analytics_callback error (data=%s): %s", data, exc)
        try:
            bot.answer_callback_query(callback.id, "⚠️ خطایی رخ داد / An error occurred.")
        except Exception:
            pass
        return True


def _edit_or_send(bot, callback: types.CallbackQuery, text: str,
                  markup: types.InlineKeyboardMarkup) -> None:
    """
    تلاش برای edit پیام موجود — اگر نشد پیام جدید می‌فرستد.
    این کار باعث می‌شود داشبورد در یک پیام ثابت باقی بماند.
    """
    try:
        bot.edit_message_text(
            text,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception:
        # پیام قابل edit نیست (خیلی قدیمی یا تغییری نکرده)
        bot.send_message(
            callback.message.chat.id,
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# بخش چهارم: گزارش روزانه خودکار
# ═══════════════════════════════════════════════════════════════════════════════

def _get_admin_chat_ids() -> list[int]:
    """لیست chat_id ادمین‌های فعال از دیتابیس."""
    try:
        rows = database.list_admins()
        return [r["user_id"] for r in rows]
    except Exception as exc:
        logger.error("[dashboard] _get_admin_chat_ids failed: %s", exc)
        return []


def send_daily_report(bot, force: bool = False) -> bool:
    """
    ارسال گزارش روزانه به همه ادمین‌ها با idempotency پایدار.

    الگوریتم (با رفع race condition):
      1. تاریخ گزارش را در REPORTING_TZ تعیین کن.
      2. برای هر ادمین، رزرو اتمیک با _try_claim_report_send انجام بده.
         این عملیات از BEGIN IMMEDIATE SQLite استفاده می‌کند و در سطح
         cross-process (نه فقط cross-thread) ایمن است.
      3. فقط ادمین‌هایی که رزرو موفق دارند پیام دریافت می‌کنند.
      4. وضعیت هر delivery را در daily_report_log ثبت کن.
      5. شکست یک ادمین ادمین‌های دیگر را متوقف نمی‌کند.

    تضمین تحویل:
      در شرایط عادی (بدون network timeout): حداکثر یک پیام به هر ادمین.
      در صورت network timeout: ممکن است Telegram پیام را پذیرفته باشد.
      Exactly-once در مقابل network failure بدون idempotency key تضمین‌شدنی
      نیست. اما race condition بین چندین process به طور کامل رفع شده است.

    force=True: idempotency را نادیده بگیر (فقط برای تست)
    Returns: True اگر حداقل یک پیام ارسال شد.
    """
    report_date = _report_date_for_now()
    text        = _build_daily_report_text(report_date)
    admin_ids   = _get_admin_chat_ids()
    sent_count  = 0
    skip_count  = 0

    for admin_id in admin_ids:
        if not force:
            # رزرو اتمیک: از race condition بین process‌ها جلوگیری می‌کند
            if not _try_claim_report_send(report_date, admin_id):
                skip_count += 1
                logger.debug(
                    "[dashboard] Daily report already claimed/delivered to admin %s for %s, skipping.",
                    admin_id, report_date,
                )
                continue

        try:
            bot.send_message(admin_id, text, parse_mode="HTML")
            _mark_report_sent(report_date, admin_id, "sent")
            sent_count += 1
        except Exception as exc:
            logger.error(
                "[dashboard] Failed to send daily report to admin %s for %s: %s",
                admin_id, report_date, exc,
            )
            # ثبت failure تا بتوان بعداً retry کرد
            _mark_report_sent(report_date, admin_id, "failed")

    logger.info(
        "[dashboard] Daily report %s: sent=%d skipped=%d failed=%d total_admins=%d.",
        report_date, sent_count, skip_count,
        len(admin_ids) - sent_count - skip_count, len(admin_ids),
    )
    return sent_count > 0


def _should_send_now(now_local: datetime) -> bool:
    """
    آیا الان وقت ارسال گزارش روزانه است؟
    پنجره: [DAILY_REPORT_HOUR:DAILY_REPORT_MINUTE, +1 دقیقه)
    """
    return (
        now_local.hour   == DAILY_REPORT_HOUR and
        now_local.minute == DAILY_REPORT_MINUTE
    )


def _daily_report_scheduler(bot) -> None:
    """
    Loop اصلی scheduler گزارش روزانه.
    در یک daemon thread مستقل اجرا می‌شود.
    هر ۶۰ ثانیه چک می‌کند که آیا وقت ارسال رسیده.

    سیاست missed window:
      اگر ربات در لحظه ۰۸:۰۰ خاموش بود و بعد از آن راه‌اندازی شود،
      اولین چرخه که ساعت از DAILY_REPORT_HOUR گذشته باشد و هنوز هیچ
      تلاشی برای آن روز ثبت نشده باشد، گزارش ارسال می‌شود.
      این رفتار یک‌بار در هر روز اتفاق می‌افتد.

    تایم‌زون:
      بررسی ساعت بر اساس REPORTING_TZ_NAME انجام می‌شود.
      مرزهای روزانه از همان تایم‌زون تبعیت می‌کنند.
    """
    tz = _get_reporting_tz()
    logger.info(
        "[dashboard] Daily report scheduler started (send at %02d:%02d %s).",
        DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE, REPORTING_TZ_NAME,
    )

    while True:
        try:
            now_local   = datetime.now(tz)
            report_date = now_local.strftime("%Y-%m-%d")

            # حالت ۱: دقیقاً در پنجره ارسال هستیم
            if _should_send_now(now_local):
                send_daily_report(bot)

            # حالت ۲: missed window — ربات بعد از ساعت ارسال راه‌اندازی شد
            elif (now_local.hour > DAILY_REPORT_HOUR or
                  (now_local.hour == DAILY_REPORT_HOUR and
                   now_local.minute > DAILY_REPORT_MINUTE)):
                if not _any_report_attempted(report_date):
                    logger.info(
                        "[dashboard] Missed window for %s — sending catch-up report.",
                        report_date,
                    )
                    send_daily_report(bot)

        except Exception as exc:
            logger.error("[dashboard] Scheduler loop error: %s", exc)

        time.sleep(60)


def start_daily_report_scheduler(bot) -> None:
    """
    راه‌اندازی thread گزارش روزانه.
    ایمن در برابر فراخوانی چندباره — فقط یک thread ایجاد می‌شود.
    حتی اگر این تابع چندبار (از چند thread) فراخوانی شود، تنها یک
    scheduler thread در حال اجرا خواهد بود.
    """
    global _scheduler_started

    # اطمینان از وجود جدول idempotency قبل از راه‌اندازی scheduler
    _init_report_log_table()

    with _scheduler_lock:
        if _scheduler_started:
            logger.warning(
                "[dashboard] start_daily_report_scheduler called more than once — ignored."
            )
            return
        _scheduler_started = True

    t = threading.Thread(
        target=_daily_report_scheduler,
        args=(bot,),
        daemon=True,
        name="daily-report-scheduler",
    )
    t.start()
    logger.info("[dashboard] Daily report scheduler thread started.")
