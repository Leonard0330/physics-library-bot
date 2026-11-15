"""
analytics.py — سیستم ردیابی رویداد و گزارش‌دهی آنالیتیکس

این ماژول کاملاً مجزا از منطق اصلی ربات است.
هیچ استثنایی از این ماژول به handler‌های اصلی propagate نمی‌شود.
همه تابع‌های log_* به صورت fire-and-forget هستند.

رویدادهای پشتیبانی‌شده:
  user_started          — کاربر /start زد
  search_submitted      — کاربر کلیدواژه جستجو ارسال کرد
  search_completed      — جستجو نتیجه داشت
  search_no_results     — جستجو نتیجه نداشت (خطای فنی نیست)
  search_error          — خطای فنی در جستجو
  file_requested        — کاربر دکمه دانلود زد
  file_sent             — فایل موفقیت‌آمیز تحویل داده شد
  file_send_failed      — ارسال فایل با خطا مواجه شد
  bot_error             — خطای مهم در handler‌های ربات

رویدادهای External Search (arXiv) — نام‌های جدا با پیشوند ext_ تا آمار جستجو و
تحویل فایل محلی (search_* / file_*) دست‌نخورده بماند. متن جستجو ذخیره نمی‌شود.
  ext_search_submitted  — کاربر عبارت جستجوی خارجی ارسال کرد       extra: field
  ext_search_completed  — جستجو نتیجه داشت                         extra: field, result_count
  ext_search_no_results — جستجو نتیجه نداشت                        extra: field
  ext_search_error      — خطای فنی (timeout/شبکه/پاسخ نامعتبر)     extra: field, kind, paged
  ext_details_viewed    — صفحه‌ی جزئیات یک نتیجه باز شد            extra: source
  ext_pdf_requested     — درخواست PDF پذیرفته شد                   extra: source
  ext_pdf_sent          — PDF معتبر ارسال شد                       extra: source
  ext_pdf_failed        — دانلود/اعتبارسنجی/ارسال PDF ناموفق بود   extra: source, reason
(این رویدادها resource_id ندارند؛ فقط برای فعالیت کاربر در get_daily_activity و retention شمرده می‌شوند.)
"""

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Optional

import database

logger = logging.getLogger(__name__)

# ── Async write queue ─────────────────────────────────────────────────────────
# رویدادها ابتدا در یک queue در حافظه قرار می‌گیرند و یک worker thread
# جداگانه آن‌ها را به دیتابیس می‌نویسد. این کار باعث می‌شود هیچ‌وقت
# write‌های analytics منتظر main thread نمانند.

_event_queue: queue.Queue = queue.Queue(maxsize=10_000)
_worker_started = False
_worker_lock = threading.Lock()


def _worker() -> None:
    """Worker thread: رویدادها رو از queue می‌خونه و به دیتابیس می‌نویسه."""
    while True:
        try:
            item = _event_queue.get(timeout=5)
            if item is None:          # sentinel برای shutdown
                break
            event_type, user_id, resource_id, extra_json = item
            _write_event(event_type, user_id, resource_id, extra_json)
        except queue.Empty:
            continue
        except Exception as exc:
            logger.error("[analytics] worker error: %s", exc)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker, daemon=True, name="analytics-worker")
        t.start()
        _worker_started = True


def _write_event(
    event_type: str,
    user_id: Optional[int],
    resource_id: Optional[int],
    extra_json: Optional[str],
) -> None:
    """نوشتن یک رویداد به دیتابیس — فقط از worker thread صدا زده می‌شه."""
    try:
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO analytics_events
                    (event_type, user_id, resource_id, extra, created_at)
                VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                """,
                (event_type, user_id, resource_id, extra_json),
            )
            # هر رویداد کاربر = فعالیت؛ last_seen فقط در /start به‌روز می‌شد و
            # کاربرانی که فقط جستجو/دانلود (محلی یا External) می‌کردند «فعال» شمرده نمی‌شدند.
            if user_id is not None and event_type != "bot_error":
                conn.execute(
                    "UPDATE users SET last_seen = datetime('now') WHERE user_id = ?",
                    (user_id,),
                )
            conn.commit()
    except Exception as exc:
        logger.error("[analytics] _write_event failed (type=%s uid=%s): %s",
                     event_type, user_id, exc)


# ── Public log API ────────────────────────────────────────────────────────────

def log_event(
    event_type: str,
    user_id: Optional[int] = None,
    resource_id: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    """
    ثبت یک رویداد analytics به صورت آسنکرون.

    هرگز exception به caller نمی‌دهد.
    اگر queue پر باشد، رویداد رد می‌شود و warning لاگ می‌شود.
    """
    _ensure_worker()
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
    try:
        _event_queue.put_nowait((event_type, user_id, resource_id, extra_json))
    except queue.Full:
        logger.warning("[analytics] event queue full, dropping event: %s uid=%s",
                       event_type, user_id)
    except Exception as exc:
        logger.error("[analytics] log_event failed (type=%s): %s", event_type, exc)


# ── Convenience wrappers ──────────────────────────────────────────────────────

def log_user_started(user_id: int, is_new: bool) -> None:
    """
    ثبت رویداد /start.
    is_new=True فقط برای اولین بار زدن /start است.
    /start های بعدی (بازگشت به خانه) با is_new=False ثبت می‌شوند.
    """
    log_event("user_started", user_id=user_id, extra={"is_new": is_new})


def log_search_submitted(user_id: int, has_filters: bool) -> None:
    """کاربر کلیدواژه جستجو را ارسال کرد — قبل از اجرای جستجو."""
    log_event("search_submitted", user_id=user_id, extra={"has_filters": has_filters})


def log_search_completed(user_id: int, result_count: int) -> None:
    """جستجو اجرا شد و نتایجی یافت شد (result_count >= 1)."""
    log_event("search_completed", user_id=user_id, extra={"result_count": result_count})


def log_search_no_results(user_id: int, has_filters: bool) -> None:
    """جستجو اجرا شد اما هیچ نتیجه‌ای نبود — خطای فنی نیست."""
    log_event("search_no_results", user_id=user_id, extra={"has_filters": has_filters})


def log_search_error(user_id: int, error_msg: str = "") -> None:
    """خطای فنی در اجرای جستجو."""
    log_event("search_error", user_id=user_id, extra={"error": error_msg[:200]})


def log_file_requested(user_id: int, resource_id: int, resource_type: str) -> None:
    """کاربر دکمه دانلود را زد — قبل از ارسال فایل."""
    log_event("file_requested", user_id=user_id, resource_id=resource_id,
              extra={"resource_type": resource_type})


def log_file_sent(user_id: int, resource_id: int, resource_type: str) -> None:
    """فایل با موفقیت تحویل داده شد."""
    log_event("file_sent", user_id=user_id, resource_id=resource_id,
              extra={"resource_type": resource_type})


def log_file_send_failed(user_id: int, resource_id: int, resource_type: str) -> None:
    """ارسال فایل با خطا مواجه شد."""
    log_event("file_send_failed", user_id=user_id, resource_id=resource_id,
              extra={"resource_type": resource_type})


def log_bot_error(user_id: Optional[int], handler: str, error_msg: str = "") -> None:
    """خطای مهم در یکی از handler‌های ربات."""
    log_event("bot_error", user_id=user_id,
              extra={"handler": handler, "error": error_msg[:300]})


# ── Analytics query layer ─────────────────────────────────────────────────────
# توابع زیر برای گزارش‌دهی استفاده می‌شوند.
# هیچ‌کدام به سرعت پاسخ‌دهی ربات حساس نیستند.

def get_user_stats() -> dict:
    """
    آمار کلی کاربران.
    total_users همیشه از جدول users می‌آید (همان تعریف database.get_library_stats)،
    و خطا در بخش‌های analytics باعث صفر شدن آن نمی‌شود.
    """
    out = {"total_users": 0, "users_with_created_at": 0,
           "users_searched_at_least_once": 0, "users_never_searched": 0,
           "users_searched_external": 0}
    try:
        with database.get_connection() as conn:
            out["total_users"] = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            try:
                out["users_with_created_at"] = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE created_at IS NOT NULL"
                ).fetchone()[0]
            except Exception as exc:
                logger.error("[analytics] get_user_stats(created_at) failed: %s", exc)
            try:
                out["users_searched_at_least_once"] = conn.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
                    "WHERE event_type = 'search_submitted'"
                ).fetchone()[0]
            except Exception as exc:
                logger.error("[analytics] get_user_stats(events) failed: %s", exc)
            try:
                out["users_searched_external"] = conn.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM analytics_events "
                    "WHERE event_type = 'ext_search_submitted'"
                ).fetchone()[0]
            except Exception as exc:
                logger.error("[analytics] get_user_stats(ext) failed: %s", exc)
        out["users_never_searched"] = max(0, out["total_users"] - out["users_searched_at_least_once"])
    except Exception as exc:
        logger.error("[analytics] get_user_stats failed: %s", exc)
    return out


def get_new_users(period: str = "day", n: int = 30) -> list[dict]:
    """
    کاربران جدید به تفکیک دوره زمانی.
    period: 'day' | 'week' | 'month'
    n: تعداد دوره‌های اخیر
    فقط کاربرانی که created_at دارند (بعد از migration).
    """
    try:
        if period == "day":
            fmt = "%Y-%m-%d"
            interval = f"-{n} days"
        elif period == "week":
            fmt = "%Y-W%W"
            interval = f"-{n * 7} days"
        else:
            fmt = "%Y-%m"
            interval = f"-{n} months"

        with database.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT strftime('{fmt}', created_at) AS period,
                       COUNT(*) AS new_users
                FROM users
                WHERE created_at IS NOT NULL
                  AND created_at >= datetime('now', '{interval}')
                GROUP BY period
                ORDER BY period ASC
                """
            ).fetchall()
        return [{"period": r["period"], "new_users": r["new_users"]} for r in rows]
    except Exception as exc:
        logger.error("[analytics] get_new_users failed: %s", exc)
        return []


def get_active_users(period: str = "day", n: int = 30) -> list[dict]:
    """
    کاربران فعال (بر اساس last_seen) به تفکیک دوره زمانی.
    period: 'day' | 'week' | 'month'
    """
    try:
        if period == "day":
            fmt = "%Y-%m-%d"
            interval = f"-{n} days"
        elif period == "week":
            fmt = "%Y-W%W"
            interval = f"-{n * 7} days"
        else:
            fmt = "%Y-%m"
            interval = f"-{n} months"

        with database.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT strftime('{fmt}', last_seen) AS period,
                       COUNT(DISTINCT user_id) AS active_users
                FROM users
                WHERE last_seen >= datetime('now', '{interval}')
                GROUP BY period
                ORDER BY period ASC
                """
            ).fetchall()
        return [{"period": r["period"], "active_users": r["active_users"]} for r in rows]
    except Exception as exc:
        logger.error("[analytics] get_active_users failed: %s", exc)
        return []


def get_search_stats() -> dict:
    """
    آمار جستجو از ابتدای analytics.
    Returns:
        total_submitted, total_completed, total_no_results, total_errors,
        unique_searchers, conversion_rate (completed/submitted)
    """
    try:
        with database.get_connection() as conn:
            submitted = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='search_submitted'"
            ).fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='search_completed'"
            ).fetchone()[0]
            no_results = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='search_no_results'"
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='search_error'"
            ).fetchone()[0]
            unique_searchers = conn.execute(
                """SELECT COUNT(DISTINCT user_id) FROM analytics_events
                   WHERE event_type='search_submitted'"""
            ).fetchone()[0]

        rate = round(completed / submitted * 100, 1) if submitted else 0.0
        return {
            "total_submitted": submitted,
            "total_completed": completed,
            "total_no_results": no_results,
            "total_errors": errors,
            "unique_searchers": unique_searchers,
            "conversion_rate_pct": rate,
        }
    except Exception as exc:
        logger.error("[analytics] get_search_stats failed: %s", exc)
        return {}


def get_delivery_stats() -> dict:
    """
    آمار تحویل فایل.
    Returns:
        total_requested, total_sent, total_failed, success_rate_pct
    """
    try:
        with database.get_connection() as conn:
            requested = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='file_requested'"
            ).fetchone()[0]
            sent = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='file_sent'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM analytics_events WHERE event_type='file_send_failed'"
            ).fetchone()[0]

        rate = round(sent / requested * 100, 1) if requested else 0.0
        return {
            "total_requested": requested,
            "total_sent": sent,
            "total_failed": failed,
            "success_rate_pct": rate,
        }
    except Exception as exc:
        logger.error("[analytics] get_delivery_stats failed: %s", exc)
        return {}


def get_daily_activity(n_days: int = 30) -> list[dict]:
    """
    فعالیت روزانه: جستجوها، دانلودها، کاربران فعال در N روز اخیر.
    """
    try:
        with database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    strftime('%Y-%m-%d', created_at) AS day,
                    COUNT(CASE WHEN event_type='search_submitted'  THEN 1 END) AS searches,
                    COUNT(CASE WHEN event_type='file_sent'         THEN 1 END) AS deliveries,
                    COUNT(CASE WHEN event_type='ext_search_submitted' THEN 1 END) AS ext_searches,
                    COUNT(CASE WHEN event_type='ext_pdf_sent'      THEN 1 END) AS ext_pdfs,
                    COUNT(DISTINCT CASE WHEN user_id IS NOT NULL   THEN user_id END) AS active_users
                FROM analytics_events
                WHERE datetime(created_at) >= datetime('now', ?)
                GROUP BY day
                ORDER BY day ASC
                """,
                (f"-{n_days} days",)
            ).fetchall()
        return [
            {
                "day": r["day"],
                "searches": r["searches"],
                "deliveries": r["deliveries"],
                "ext_searches": r["ext_searches"],
                "ext_pdfs": r["ext_pdfs"],
                "active_users": r["active_users"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("[analytics] get_daily_activity failed: %s", exc)
        return []


def get_retention_cohort(cohort_day: int) -> dict:
    """
    محاسبه retention برای یک روز مشخص پس از ثبت‌نام.
    cohort_day: 1 | 7 | 30

    تعریف retention: کاربری که در روز X پس از created_at، حداقل
    یک رویداد در analytics_events داشته باشد.

    فقط برای کاربرانی که created_at دارند و به اندازه کافی وقت گذشته.
    """
    try:
        with database.get_connection() as conn:
            # کاربران واجد شرایط: created_at دارند و حداقل (cohort_day+1) روز گذشته.
            # پنجره فعالیت [cohort_day, cohort_day+1) روز بعد از ثبت‌نام باید کاملاً
            # در گذشته باشد تا cohort نهایی تلقی شود — به همین دلیل آستانه
            # cohort_day+1 است نه cohort_day.
            eligible = conn.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE created_at IS NOT NULL
                  AND created_at <= datetime('now', ?)
                """,
                (f"-{cohort_day + 1} days",)
            ).fetchone()[0]

            if eligible == 0:
                return {
                    "cohort_day": cohort_day,
                    "eligible_users": 0,
                    "retained_users": 0,
                    "retention_rate_pct": None,
                    "note": "No eligible users yet",
                }

            # کاربرانی که در پنجره [cohort_day, cohort_day+1) روز بعد از created_at فعال بودند
            retained = conn.execute(
                """
                SELECT COUNT(DISTINCT u.user_id)
                FROM users u
                INNER JOIN analytics_events ae ON ae.user_id = u.user_id
                WHERE u.created_at IS NOT NULL
                  AND u.created_at <= datetime('now', ?)
                  AND datetime(ae.created_at) >= datetime(u.created_at, ?)
                  AND datetime(ae.created_at) <  datetime(u.created_at, ?)
                """,
                (
                    f"-{cohort_day} days",
                    f"+{cohort_day} days",
                    f"+{cohort_day + 1} days",
                )
            ).fetchone()[0]

        rate = round(retained / eligible * 100, 1) if eligible else None
        return {
            "cohort_day": cohort_day,
            "eligible_users": eligible,
            "retained_users": retained,
            "retention_rate_pct": rate,
        }
    except Exception as exc:
        logger.error("[analytics] get_retention_cohort(day=%s) failed: %s", cohort_day, exc)
        return {}


def get_first_seen_last_active(user_id: int) -> dict:
    """
    زمان اولین ثبت‌نام و آخرین فعالیت یک کاربر.
    """
    try:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT created_at, last_seen FROM users WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            if not row:
                return {}
            first_event = conn.execute(
                """SELECT MIN(created_at) AS first_event
                   FROM analytics_events WHERE user_id = ?""",
                (user_id,)
            ).fetchone()

        return {
            "user_id": user_id,
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "first_analytics_event": first_event["first_event"] if first_event else None,
        }
    except Exception as exc:
        logger.error("[analytics] get_first_seen_last_active(%s) failed: %s", user_id, exc)
        return {}


def get_search_count_for_user(user_id: int) -> int:
    """تعداد جستجوهای یک کاربر خاص."""
    try:
        with database.get_connection() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM analytics_events
                   WHERE event_type='search_submitted' AND user_id=?""",
                (user_id,)
            ).fetchone()[0]
    except Exception as exc:
        logger.error("[analytics] get_search_count_for_user failed: %s", exc)
        return 0


def get_new_users_in_period(days: int, offset_days: int = 0) -> int:
    """
    تعداد کاربران جدید در N روز اخیر.
    offset_days=1 → دیروز (برای مقایسه در گزارش روزانه).
    فقط کاربرانی که created_at دارند شمرده می‌شوند.
    """
    try:
        with database.get_connection() as conn:
            if offset_days:
                return conn.execute(
                    """SELECT COUNT(*) FROM users
                       WHERE created_at IS NOT NULL
                         AND created_at >= datetime('now', ?)
                         AND created_at <  datetime('now', ?)""",
                    (f"-{days + offset_days} days", f"-{offset_days} days")
                ).fetchone()[0]
            return conn.execute(
                """SELECT COUNT(*) FROM users
                   WHERE created_at IS NOT NULL
                     AND created_at >= datetime('now', ?)""",
                (f"-{days} days",)
            ).fetchone()[0]
    except Exception as exc:
        logger.error("[analytics] get_new_users_in_period failed: %s", exc)
        return 0


def get_active_users_in_period(days: int, offset_days: int = 0) -> int:
    """
    کاربران فعال (بر اساس last_seen) در N روز اخیر.
    """
    try:
        with database.get_connection() as conn:
            if offset_days:
                return conn.execute(
                    """SELECT COUNT(DISTINCT user_id) FROM users
                       WHERE last_seen >= datetime('now', ?)
                         AND last_seen <  datetime('now', ?)""",
                    (f"-{days + offset_days} days", f"-{offset_days} days")
                ).fetchone()[0]
            return conn.execute(
                """SELECT COUNT(DISTINCT user_id) FROM users
                   WHERE last_seen >= datetime('now', ?)""",
                (f"-{days} days",)
            ).fetchone()[0]
    except Exception as exc:
        logger.error("[analytics] get_active_users_in_period failed: %s", exc)
        return 0


def get_search_stats_in_period(days: Optional[int] = None,
                                offset_days: int = 0) -> dict:
    """
    آمار جستجو در یک بازه زمانی.
    days=None → تمام تاریخ.
    """
    try:
        with database.get_connection() as conn:
            where, params = _period_where("created_at", days, offset_days)

            submitted = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='search_submitted' {where}", params
            ).fetchone()[0]
            completed = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='search_completed' {where}", params
            ).fetchone()[0]
            no_results = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='search_no_results' {where}", params
            ).fetchone()[0]
            errors = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='search_error' {where}", params
            ).fetchone()[0]
            unique = conn.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM analytics_events "
                f"WHERE event_type='search_submitted' {where}", params
            ).fetchone()[0]

        rate = round(completed / submitted * 100, 1) if submitted else 0.0
        return {
            "total_submitted":    submitted,
            "total_completed":    completed,
            "total_no_results":   no_results,
            "total_errors":       errors,
            "unique_searchers":   unique,
            "conversion_rate_pct": rate,
        }
    except Exception as exc:
        logger.error("[analytics] get_search_stats_in_period failed: %s", exc)
        return {}


def get_delivery_stats_in_period(days: Optional[int] = None,
                                  offset_days: int = 0) -> dict:
    """
    آمار تحویل فایل در یک بازه زمانی.
    """
    try:
        with database.get_connection() as conn:
            where, params = _period_where("created_at", days, offset_days)

            requested = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='file_requested' {where}", params
            ).fetchone()[0]
            sent = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='file_sent' {where}", params
            ).fetchone()[0]
            failed = conn.execute(
                f"SELECT COUNT(*) FROM analytics_events "
                f"WHERE event_type='file_send_failed' {where}", params
            ).fetchone()[0]

        rate = round(sent / requested * 100, 1) if requested else 0.0
        return {
            "total_requested":  requested,
            "total_sent":       sent,
            "total_failed":     failed,
            "success_rate_pct": rate,
        }
    except Exception as exc:
        logger.error("[analytics] get_delivery_stats_in_period failed: %s", exc)
        return {}


EXT_EVENT_TYPES = (
    "ext_search_submitted", "ext_search_completed", "ext_search_no_results", "ext_search_error",
    "ext_details_viewed", "ext_pdf_requested", "ext_pdf_sent", "ext_pdf_failed",
)


def get_external_search_stats_in_period(days: Optional[int] = None,
                                        offset_days: int = 0) -> dict:
    """
    آمار External Search در یک بازه زمانی (days=None → تمام تاریخ).
    فقط رویدادهای ext_* را می‌شمارد و با آمار جستجوی محلی قاطی نمی‌شود.
    """
    try:
        with database.get_connection() as conn:
            where, params = _period_where("created_at", days, offset_days)
            marks = ",".join("?" for _ in EXT_EVENT_TYPES)
            rows = conn.execute(
                f"SELECT event_type, COUNT(*) AS n FROM analytics_events "
                f"WHERE event_type IN ({marks}) {where} GROUP BY event_type",
                [*EXT_EVENT_TYPES, *params],
            ).fetchall()
            unique = conn.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM analytics_events "
                f"WHERE event_type='ext_search_submitted' {where}", params
            ).fetchone()[0]
        c = {r["event_type"]: r["n"] for r in rows}
        submitted, completed = c.get("ext_search_submitted", 0), c.get("ext_search_completed", 0)
        requested, sent = c.get("ext_pdf_requested", 0), c.get("ext_pdf_sent", 0)
        return {
            "total_submitted":   submitted,
            "total_completed":   completed,
            "total_no_results":  c.get("ext_search_no_results", 0),
            "total_errors":      c.get("ext_search_error", 0),
            "unique_searchers":  unique,
            "details_viewed":    c.get("ext_details_viewed", 0),
            "pdf_requested":     requested,
            "pdf_sent":          sent,
            "pdf_failed":        c.get("ext_pdf_failed", 0),
            "search_success_rate_pct": round(completed / submitted * 100, 1) if submitted else 0.0,
            "pdf_success_rate_pct":    round(sent / requested * 100, 1) if requested else 0.0,
        }
    except Exception as exc:
        logger.error("[analytics] get_external_search_stats_in_period failed: %s", exc)
        return {}


def _period_where(col: str, days: Optional[int], offset_days: int = 0):
    """
    ساخت WHERE clause و params برای فیلتر بازه زمانی.

    برای offset_days=1 ("دیروز"):
      شروع: now - (days + offset_days) days   e.g. now-2d
      پایان: now - offset_days days            e.g. now-1d  ← قبلاً now-0 بود (bug)

    مثال: days=1, offset_days=1 → [now-2d, now-1d)   = دیروز
           days=1, offset_days=0 → [now-1d, now)      = امروز
    """
    if days is None:
        return "", []
    if offset_days:
        return (
            f"AND datetime({col}) >= datetime('now', ?) AND datetime({col}) < datetime('now', ?)",
            [f"-{days + offset_days} days", f"-{offset_days} days"]
        )
    return f"AND datetime({col}) >= datetime('now', ?)", [f"-{days} days"]


def get_growth_series(period: str = "day", n: int = 30) -> list[dict]:
    """
    رشد تجمعی کاربران در N دوره اخیر.
    شامل new_users (تجمعی) و total_at_end_of_period.
    """
    try:
        new_per_period = get_new_users(period=period, n=n)
        # محاسبه تجمعی
        result = []
        for entry in new_per_period:
            result.append({
                "period": entry["period"],
                "new_users": entry["new_users"],
            })
        return result
    except Exception as exc:
        logger.error("[analytics] get_growth_series failed: %s", exc)
        return []


def get_summary_report() -> dict:
    """
    گزارش خلاصه برای داشبورد ادمین — ترکیب همه metrics اصلی.
    """
    return {
        "users": get_user_stats(),
        "searches": get_search_stats(),
        "deliveries": get_delivery_stats(),
        "external": get_external_search_stats_in_period(),
        "retention": {
            "day1":  get_retention_cohort(1),
            "day7":  get_retention_cohort(7),
            "day30": get_retention_cohort(30),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
