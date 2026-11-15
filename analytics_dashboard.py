"""
analytics_dashboard.py — داشبورد Analytics در Telegram

این ماژول رابط کاربری Telegram برای داشبورد analytics را پیاده‌سازی می‌کند.
تمام منطق محاسبات از analytics.py خوانده می‌شود.
تمام callback‌ها با prefix "adm_anl:" هستند تا با سایر callback‌های ادمین تداخل نداشته باشند.

ساختار منو:
  📊 داشبورد Analytics
    ├── 📊 خلاصه کلی
    ├── 👥 آمار کاربران
    │     └── [فیلتر بازه زمانی]
    ├── 🔍 آمار جستجو
    │     └── [فیلتر بازه زمانی]
    ├── 📥 آمار تحویل
    │     └── [فیلتر بازه زمانی]
    ├── 🌍 جست‌وجوی خارجی (arXiv: جست‌وجو، جزئیات، PDF)
    │     └── [فیلتر بازه زمانی]
    ├── 🔄 Retention
    └── 📈 رشد

گزارش امروز:
  - فقط on-demand: با دکمه‌ی «گزارش امروز» به همان ادمین درخواست‌دهنده ارسال می‌شود.
  - scheduler و گزارش روزانه‌ی خودکار (daily_report_log) حذف شده‌اند.

تایم‌زون:
  - پیش‌فرض: UTC
  - قابل تغییر از طریق REPORTING_TZ (نام IANA مانند 'Asia/Tehran')
  - تمام timestamps در دیتابیس UTC هستند
  - مرز روزانه برای گزارش‌دهی بر اساس REPORTING_TZ محاسبه می‌شود
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from telebot import types
import database
import analytics

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# رشته‌های رابط کاربری — دو زبانه
# ═══════════════════════════════════════════════════════════════════════════════

STRINGS: dict[str, dict[str, str]] = {
    # ── period labels ──────────────────────────────────────────────────────────
    "period_today":   {"fa": "امروز",        "en": "Today"},
    "period_week":    {"fa": "۷ روز اخیر",   "en": "Last 7 days"},
    "period_month":   {"fa": "۳۰ روز اخیر",  "en": "Last 30 days"},
    "period_quarter": {"fa": "۹۰ روز اخیر",  "en": "Last 90 days"},
    "period_all":     {"fa": "کل تاریخ",     "en": "All time"},
    # ── growth period labels ───────────────────────────────────────────────────
    "growth_day":     {"fa": "روزانه",        "en": "Daily"},
    "growth_week":    {"fa": "هفتگی",         "en": "Weekly"},
    "growth_month":   {"fa": "ماهانه",        "en": "Monthly"},
    # ── main menu buttons ──────────────────────────────────────────────────────
    "btn_overview":   {"fa": "📊 خلاصه کلی",  "en": "📊 Overview"},
    "btn_users":      {"fa": "👥 کاربران",     "en": "👥 Users"},
    "btn_search":     {"fa": "🔍 جستجو",       "en": "🔍 Search"},
    "btn_delivery":   {"fa": "📥 تحویل فایل", "en": "📥 File Delivery"},
    "btn_external":   {"fa": "🌍 جست‌وجوی خارجی", "en": "🌍 External Search"},
    "btn_retention":  {"fa": "🔄 Retention",   "en": "🔄 Retention"},
    "btn_growth":     {"fa": "📈 رشد",         "en": "📈 Growth"},
    "btn_back_panel": {"fa": "🔙 بازگشت به پنل", "en": "🔙 Back to Panel"},
    "btn_refresh":    {"fa": "🔄 بروزرسانی",   "en": "🔄 Refresh"},
    "btn_dashboard":  {"fa": "🔙 داشبورد",     "en": "🔙 Dashboard"},
    "btn_today_report": {"fa": "📋 گزارش امروز", "en": "📋 Today's Report"},
    # ── period filter button labels ────────────────────────────────────────────
    "btn_today":      {"fa": "امروز",          "en": "Today"},
    "btn_7d":         {"fa": "۷ روز",          "en": "7 days"},
    "btn_30d":        {"fa": "۳۰ روز",         "en": "30 days"},
    "btn_90d":        {"fa": "۹۰ روز",         "en": "90 days"},
    "btn_all":        {"fa": "همه",            "en": "All"},
    # ── section headers ────────────────────────────────────────────────────────
    "header_overview":  {"fa": "📊 داشبورد Analytics — خلاصه کلی",   "en": "📊 Analytics Dashboard — Overview"},
    "header_users":     {"fa": "👥 آمار کاربران",                     "en": "👥 User Stats"},
    "header_search":    {"fa": "🔍 آمار جستجو",                       "en": "🔍 Search Stats"},
    "header_delivery":  {"fa": "📥 آمار تحویل فایل",                  "en": "📥 File Delivery Stats"},
    "header_external":  {"fa": "🌍 آمار جست‌وجوی خارجی",               "en": "🌍 External Search Stats"},
    "header_retention": {"fa": "🔄 Retention کاربران",                "en": "🔄 User Retention"},
    "header_growth":    {"fa": "📈 رشد — نمای",                       "en": "📈 Growth —"},
    "header_daily":     {"fa": "📋 گزارش روزانه",                     "en": "📋 Daily Report"},
    # ── dashboard entry message ────────────────────────────────────────────────
    "dashboard_title":  {"fa": "📊 داشبورد Analytics",                "en": "📊 Analytics Dashboard"},
    "dashboard_prompt": {"fa": "یک بخش را انتخاب کنید:",             "en": "Select a section:"},
    # ── overview fields ────────────────────────────────────────────────────────
    "ov_users_section":   {"fa": "👥 کاربران",        "en": "👥 Users"},
    "ov_total_reg":       {"fa": "کل ثبت‌نام‌شده",    "en": "Total registered"},
    "ov_new_today":       {"fa": "جدید امروز",        "en": "New today"},
    "ov_new_7d":          {"fa": "جدید ۷ روز",        "en": "New (7d)"},
    "ov_new_30d":         {"fa": "جدید ۳۰ روز",       "en": "New (30d)"},
    "ov_active_today":    {"fa": "فعال امروز",         "en": "Active today"},
    "ov_active_7d":       {"fa": "فعال ۷ روز",         "en": "Active (7d)"},
    "ov_active_30d":      {"fa": "فعال ۳۰ روز",        "en": "Active (30d)"},
    "ov_search_section":  {"fa": "🔍 جستجو",           "en": "🔍 Search"},
    "ov_total_searches":  {"fa": "کل جستجوها",         "en": "Total searches"},
    "ov_success_rate":    {"fa": "نرخ موفقیت",         "en": "Success rate"},
    "ov_ext_section":     {"fa": "🌍 جست‌وجوی خارجی", "en": "🌍 External Search"},
    "ov_ext_pdf_sent":    {"fa": "PDF ارسال‌شده",       "en": "PDFs sent"},
    "ov_delivery_section":{"fa": "📥 تحویل فایل",      "en": "📥 File Delivery"},
    "ov_total_requests":  {"fa": "کل درخواست‌ها",      "en": "Total requests"},
    # ── user stats fields ──────────────────────────────────────────────────────
    "us_total":           {"fa": "کل کاربران ثبت‌شده",  "en": "Total registered"},
    "us_new":             {"fa": "کاربران جدید",         "en": "New users"},
    "us_active":          {"fa": "کاربران فعال",         "en": "Active users"},
    "us_behaviour":       {"fa": "📊 رفتار جستجو",       "en": "📊 Search Behaviour"},
    "us_since":           {"fa": "از تاریخ راه‌اندازی analytics", "en": "since analytics launch"},
    "us_at_least_one":    {"fa": "حداقل یک جستجو",       "en": "At least one search"},
    "us_never":           {"fa": "هرگز جستجو نکرده",     "en": "Never searched"},
    "us_conversion":      {"fa": "نرخ تبدیل جستجو",      "en": "Search conversion"},
    "us_note1":           {"fa": "⚠️ نرخ تبدیل = کاربران با حداقل یک جستجوی ثبت‌شده ÷ کل کاربران.",
                           "en": "⚠️ Conversion rate = users with ≥1 logged search ÷ total users."},
    "us_note2":           {"fa": "کاربران قدیمی‌تر از راه‌اندازی analytics به صورت خودکار «جستجو‌نکرده» محسوب نمی‌شوند.",
                           "en": "Users predating analytics launch are not auto-counted as non-searchers."},
    "us_note3_prefix":    {"fa": "کاربران با تاریخ ثبت‌نام معتبر:",
                           "en": "Users with valid registration date:"},
    # ── search stats fields ───────────────────────────────────────────────────
    "ss_submitted":       {"fa": "کل جستجوهای ارسال‌شده",      "en": "Total submitted"},
    "ss_completed":       {"fa": "جستجوهای موفق (نتیجه داشت)", "en": "Successful (with results)"},
    "ss_no_results":      {"fa": "جستجو بدون نتیجه",           "en": "No results"},
    "ss_errors":          {"fa": "خطای فنی جستجو",             "en": "Technical errors"},
    "ss_unique":          {"fa": "کاربران یکتای جستجوکننده",   "en": "Unique searchers"},
    "ss_avg":             {"fa": "میانگین جستجو per کاربر",    "en": "Avg searches per user"},
    "ss_rate":            {"fa": "نرخ موفقیت جستجو",           "en": "Search success rate"},
    # ── delivery stats fields ─────────────────────────────────────────────────
    "ds_requested":       {"fa": "کل درخواست‌های دانلود",  "en": "Total download requests"},
    "ds_sent":            {"fa": "تحویل موفق",              "en": "Successful deliveries"},
    "ds_failed":          {"fa": "تحویل ناموفق",            "en": "Failed deliveries"},
    "ds_rate":            {"fa": "نرخ موفقیت تحویل",        "en": "Delivery success rate"},
    # ── external search fields ────────────────────────────────────────────────
    "es_submitted":       {"fa": "کل جست‌وجوهای ارسال‌شده",   "en": "Total submitted"},
    "es_completed":       {"fa": "جست‌وجوهای موفق (نتیجه داشت)", "en": "Successful (with results)"},
    "es_no_results":      {"fa": "جست‌وجو بدون نتیجه",         "en": "No results"},
    "es_errors":          {"fa": "خطای فنی جست‌وجو",           "en": "Technical errors"},
    "es_unique":          {"fa": "کاربران یکتای جست‌وجوکننده", "en": "Unique searchers"},
    "es_rate":            {"fa": "نرخ موفقیت جست‌وجو",         "en": "Search success rate"},
    "es_details":         {"fa": "جزئیات بازشده",              "en": "Details opened"},
    "es_pdf_section":     {"fa": "📄 PDF",                      "en": "📄 PDF"},
    "es_pdf_requested":   {"fa": "درخواست PDF",                 "en": "PDF requests"},
    "es_pdf_sent":        {"fa": "ارسال موفق",                  "en": "Sent"},
    "es_pdf_failed":      {"fa": "ارسال ناموفق",                "en": "Failed"},
    "es_pdf_rate":        {"fa": "نرخ موفقیت PDF",              "en": "PDF success rate"},
    "es_note":            {"fa": "ℹ️ جدا از آمار جستجو و تحویل فایل کتابخانه‌ی محلی؛ متن جست‌وجو ذخیره نمی‌شود.",
                           "en": "ℹ️ Separate from local library search and delivery stats; query text is not stored."},
    # ── retention ─────────────────────────────────────────────────────────────
    "ret_no_data":        {"fa": "داده کافی وجود ندارد",    "en": "Insufficient data"},
    "ret_day1":           {"fa": "روز ۱",   "en": "Day 1"},
    "ret_day7":           {"fa": "روز ۷",   "en": "Day 7"},
    "ret_day30":          {"fa": "روز ۳۰",  "en": "Day 30"},
    "ret_note1":          {"fa": "تعریف: کاربری retained محسوب می‌شود که در بازه‌ی ۲۴ ساعته‌ی روز X پس از ثبت‌نام (از X×۲۴ تا (X+۱)×۲۴ ساعت بعد)، حداقل یک رویداد داشته باشد.",
                           "en": "Definition: a user is retained if they had at least one event in the 24-hour window of day X after registration (X×24h to (X+1)×24h)."},
    "ret_note2":          {"fa": "cohort‌های ناقص (کمتر از X روز گذشته) در محاسبه نهایی حذف می‌شوند.",
                           "en": "Incomplete cohorts (less than X days elapsed) are excluded from the final calculation."},
    "ret_note3":          {"fa": "فقط کاربران ثبت‌شده بعد از راه‌اندازی analytics واجد شرایط هستند.",
                           "en": "Only users registered after analytics launch are eligible."},
    # ── growth ────────────────────────────────────────────────────────────────
    "growth_no_data":     {"fa": "داده کافی برای نمایش وجود ندارد.", "en": "Not enough data to display."},
    "growth_new":         {"fa": "جدید",   "en": "New"},
    "growth_active":      {"fa": "فعال",   "en": "Active"},
    # ── daily report ─────────────────────────────────────────────────────────
    "dr_users":           {"fa": "👥 کاربران",        "en": "👥 Users"},
    "dr_new":             {"fa": "جدید",              "en": "New"},
    "dr_active":          {"fa": "فعال",              "en": "Active"},
    "dr_search":          {"fa": "🔍 جستجو",          "en": "🔍 Search"},
    "dr_total":           {"fa": "کل جستجو",          "en": "Total searches"},
    "dr_searchers":       {"fa": "کاربران جستجوکننده","en": "Searchers"},
    "dr_with_results":    {"fa": "با نتیجه",          "en": "With results"},
    "dr_no_results":      {"fa": "بدون نتیجه",        "en": "No results"},
    "dr_errors":          {"fa": "خطای فنی",          "en": "Errors"},
    "dr_ext":             {"fa": "🌍 جست‌وجوی خارجی", "en": "🌍 External Search"},
    "dr_ext_pdf":         {"fa": "PDF ارسال‌شده",       "en": "PDFs sent"},
    "us_ext_searched":    {"fa": "جست‌وجوی خارجی (کاربر یکتا)", "en": "External search (unique users)"},
    "dr_delivery":        {"fa": "📥 تحویل فایل",     "en": "📥 File Delivery"},
    "dr_success":         {"fa": "تحویل موفق",        "en": "Successful"},
    "dr_failed":          {"fa": "تحویل ناموفق",      "en": "Failed"},
    "dr_delta_note":      {"fa": "↑↓ تفاوت نسبت به دیروز | ═ بدون تغییر",
                           "en": "↑↓ vs yesterday | ═ no change"},
    # ── error ─────────────────────────────────────────────────────────────────
    "error_occurred":     {"fa": "⚠️ خطایی رخ داد.", "en": "⚠️ An error occurred."},
}


def _t(uid: int, key: str) -> str:
    """زبان کاربر را از دیتابیس می‌خواند و رشته متناسب را برمی‌گرداند."""
    lang = database.get_user_lang(uid) or "fa"
    entry = STRINGS.get(key, {})
    return entry.get(lang) or entry.get("fa", key)


# تایم‌زون گزارش‌دهی — نام IANA یا 'UTC'
# برای تغییر: REPORTING_TZ_NAME = 'Asia/Tehran'
# پیش‌فرض UTC: تمام مرزهای روزانه UTC هستند
REPORTING_TZ_NAME: str = "UTC"

# ── Callback prefix
PREFIX = "adm_anl"



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


# part1: text makers

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


def _period_label(period_key: str, uid: int) -> str:
    """برچسب بازه زمانی بر اساس زبان کاربر."""
    key_map = {
        "today":   "period_today",
        "week":    "period_week",
        "month":   "period_month",
        "quarter": "period_quarter",
        "all":     "period_all",
    }
    return _t(uid, key_map.get(period_key, "period_all"))


def _safe_div(a: int, b: int) -> float:
    return round(a / b * 100, 1) if b else 0.0


# Brief Report:

def _build_overview_text(uid: int) -> str:
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
    x  = analytics.get_external_search_stats_in_period()
    T  = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_overview')}</b>\n",
        f"<b>{T('ov_users_section')}</b>",
        f"  {T('ov_total_reg')}:    <b>{_n(u.get('total_users'))}</b>",
        f"  {T('ov_new_today')}:    <b>{_n(nu_today)}</b>",
        f"  {T('ov_new_7d')}:       <b>{_n(nu_week)}</b>",
        f"  {T('ov_new_30d')}:      <b>{_n(nu_month)}</b>",
        f"  {T('ov_active_today')}: <b>{_n(au_today)}</b>",
        f"  {T('ov_active_7d')}:    <b>{_n(au_week)}</b>",
        f"  {T('ov_active_30d')}:   <b>{_n(au_month)}</b>",
        "",
        f"<b>{T('ov_search_section')}</b>",
        f"  {T('ov_total_searches')}: <b>{_n(s.get('total_submitted'))}</b>",
        f"  {T('ov_success_rate')}:   <b>{_pct(s.get('conversion_rate_pct'))}</b>",
        "",
        f"<b>{T('ov_delivery_section')}</b>",
        f"  {T('ov_total_requests')}: <b>{_n(d.get('total_requested'))}</b>",
        f"  {T('ov_success_rate')}:   <b>{_pct(d.get('success_rate_pct'))}</b>",
        "",
        f"<b>{T('ov_ext_section')}</b>",
        f"  {T('ov_total_searches')}: <b>{_n(x.get('total_submitted'))}</b>",
        f"  {T('ov_success_rate')}:   <b>{_pct(x.get('search_success_rate_pct'))}</b>",
        f"  {T('ov_ext_pdf_sent')}:   <b>{_n(x.get('pdf_sent'))}</b>",
    ]
    return "\n".join(lines)


# User Statistics

def _build_user_stats_text(period_key: str = "all", uid: int = 0) -> str:
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
    T           = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_users')} — {_period_label(period_key, uid)}</b>\n",
        f"{T('us_total')}:  <b>{_n(total)}</b>",
        f"{T('us_new')}:    <b>{_n(nu)}</b>",
        f"{T('us_active')}: <b>{_n(au)}</b>",
        "",
        f"<b>{T('us_behaviour')}</b> <i>({T('us_since')})</i>",
        f"{T('us_at_least_one')}: <b>{_n(searched)}</b>",
        f"{T('us_never')}:        <b>{_n(never)}</b>",
        f"{T('us_ext_searched')}: <b>{_n(u.get('users_searched_external'))}</b>",
        f"{T('us_conversion')}:   <b>{_pct(conv)}</b>",
        "",
        f"<i>{T('us_note1')}</i>",
        f"<i>{T('us_note2')}</i>",
        f"<i>{T('us_note3_prefix')} {_n(with_hist)}</i>",
    ]
    return "\n".join(lines)


# Search Stats:

def _build_search_stats_text(period_key: str = "all", uid: int = 0) -> str:
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    days = days_map.get(period_key)
    s = analytics.get_search_stats_in_period(days=days)

    submitted   = s.get("total_submitted", 0)
    completed   = s.get("total_completed", 0)
    no_results  = s.get("total_no_results", 0)
    errors      = s.get("total_errors", 0)
    unique      = s.get("unique_searchers", 0)
    avg         = round(submitted / unique, 1) if unique else 0
    T           = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_search')} — {_period_label(period_key, uid)}</b>\n",
        f"{T('ss_submitted')}:  <b>{_n(submitted)}</b>",
        f"{T('ss_completed')}: <b>{_n(completed)}</b>",
        f"{T('ss_no_results')}: <b>{_n(no_results)}</b>",
        f"{T('ss_errors')}:     <b>{_n(errors)}</b>",
        f"{T('ss_unique')}:     <b>{_n(unique)}</b>",
        f"{T('ss_avg')}:        <b>{avg}</b>",
        f"{T('ss_rate')}:       <b>{_pct(s.get('conversion_rate_pct'))}</b>",
    ]
    return "\n".join(lines)


# ── آمار تحویل فایل 

def _build_delivery_stats_text(period_key: str = "all", uid: int = 0) -> str:
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    days = days_map.get(period_key)
    d = analytics.get_delivery_stats_in_period(days=days)
    T = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_delivery')} — {_period_label(period_key, uid)}</b>\n",
        f"{T('ds_requested')}: <b>{_n(d.get('total_requested'))}</b>",
        f"{T('ds_sent')}:      <b>{_n(d.get('total_sent'))}</b>",
        f"{T('ds_failed')}:    <b>{_n(d.get('total_failed'))}</b>",
        f"{T('ds_rate')}:      <b>{_pct(d.get('success_rate_pct'))}</b>",
    ]
    return "\n".join(lines)


# ── External Search

def _build_external_stats_text(period_key: str = "all", uid: int = 0) -> str:
    days_map = {"today": 1, "week": 7, "month": 30, "quarter": 90}
    s = analytics.get_external_search_stats_in_period(days=days_map.get(period_key))
    T = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_external')} — {_period_label(period_key, uid)}</b>\n",
        f"{T('es_submitted')}:  <b>{_n(s.get('total_submitted'))}</b>",
        f"{T('es_completed')}: <b>{_n(s.get('total_completed'))}</b>",
        f"{T('es_no_results')}: <b>{_n(s.get('total_no_results'))}</b>",
        f"{T('es_errors')}:     <b>{_n(s.get('total_errors'))}</b>",
        f"{T('es_unique')}:     <b>{_n(s.get('unique_searchers'))}</b>",
        f"{T('es_rate')}:       <b>{_pct(s.get('search_success_rate_pct'))}</b>",
        f"{T('es_details')}:    <b>{_n(s.get('details_viewed'))}</b>",
        "",
        f"<b>{T('es_pdf_section')}</b>",
        f"  {T('es_pdf_requested')}: <b>{_n(s.get('pdf_requested'))}</b>",
        f"  {T('es_pdf_sent')}:      <b>{_n(s.get('pdf_sent'))}</b>",
        f"  {T('es_pdf_failed')}:    <b>{_n(s.get('pdf_failed'))}</b>",
        f"  {T('es_pdf_rate')}:      <b>{_pct(s.get('pdf_success_rate_pct'))}</b>",
        "",
        f"<i>{T('es_note')}</i>",
    ]
    return "\n".join(lines)


# ── Retention 

def _build_retention_text(uid: int = 0) -> str:
    r1  = analytics.get_retention_cohort(1)
    r7  = analytics.get_retention_cohort(7)
    r30 = analytics.get_retention_cohort(30)
    T   = lambda k: _t(uid, k)

    def _fmt_cohort(r: dict, label: str) -> str:
        eligible = r.get("eligible_users", 0)
        retained = r.get("retained_users", 0)
        rate     = r.get("retention_rate_pct")
        if eligible == 0:
            return f"{label}: <i>{T('ret_no_data')}</i>"
        return (
            f"{label}: <b>{_pct(rate)}</b>"
            f"  <i>({_n(retained)} / {_n(eligible)})</i>"
        )

    lines = [
        f"<b>{T('header_retention')}</b>\n",
        _fmt_cohort(r1,  T("ret_day1")),
        _fmt_cohort(r7,  T("ret_day7")),
        _fmt_cohort(r30, T("ret_day30")),
        "",
        f"<i>{T('ret_note1')}</i>",
        f"<i>{T('ret_note2')}</i>",
        f"<i>{T('ret_note3')}</i>",
    ]
    return "\n".join(lines)


# ── growth

def _build_growth_text(period: str = "day", uid: int = 0) -> str:
    period_key_map = {"day": "growth_day", "week": "growth_week", "month": "growth_month"}
    n_map = {"day": 14, "week": 8, "month": 6}
    n = n_map.get(period, 14)
    T = lambda k: _t(uid, k)

    rows = analytics.get_new_users(period=period, n=n)
    act  = analytics.get_active_users(period=period, n=n)

    act_map = {r["period"]: r["active_users"] for r in act}

    label = T(period_key_map.get(period, "growth_day"))
    lines = [f"<b>{T('header_growth')} {label}</b>\n"]

    if not rows:
        lines.append(f"<i>{T('growth_no_data')}</i>")
        return "\n".join(lines)

    for r in rows[-10:]:
        p     = r["period"]
        new_u = r["new_users"]
        act_u = act_map.get(p, 0)
        lines.append(f"<code>{p}</code>  {T('growth_new')}: <b>{_n(new_u)}</b>  {T('growth_active')}: <b>{_n(act_u)}</b>")

    return "\n".join(lines)


# ── Daily Report

def _build_daily_report_text(report_date: str, uid: int = 0) -> str:
    """
    ساخت متن گزارش روزانه برای یک تاریخ مشخص.
    report_date: 'YYYY-MM-DD' UTC
    uid: شناسه ادمین دریافت‌کننده — زبان گزارش بر اساس زبان او تنظیم می‌شود.
    """
    # days=1, offset_days=0  → [now-1d, now)      = امروز (۲۴ ساعت اخیر)
    # days=1, offset_days=1  → [now-2d, now-1d)   = دیروز (۲۴ ساعت معادل)
    # استفاده از days=2 با offset=1 اشتباه بود: پنجره ۴۸ ساعته می‌داد،
    # که قابل مقایسه با پنجره ۲۴ ساعته امروز نیست.
    today_s = analytics.get_search_stats_in_period(days=1)
    yest_s  = analytics.get_search_stats_in_period(days=1, offset_days=1)
    today_d = analytics.get_delivery_stats_in_period(days=1)
    yest_d  = analytics.get_delivery_stats_in_period(days=1, offset_days=1)

    today_x = analytics.get_external_search_stats_in_period(days=1)
    yest_x  = analytics.get_external_search_stats_in_period(days=1, offset_days=1)

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

    T = lambda k: _t(uid, k)

    lines = [
        f"<b>{T('header_daily')} — {report_date} UTC</b>\n",
        f"<b>{T('dr_users')}</b>",
        f"  {T('dr_new')}:    <b>{_n(nu_today)}</b>{_delta(nu_today, nu_yest)}",
        f"  {T('dr_active')}: <b>{_n(au_today)}</b>{_delta(au_today, au_yest)}",
        "",
        f"<b>{T('dr_search')}</b>",
        f"  {T('dr_total')}:       <b>{_n(t_sub)}</b>{_delta(t_sub, y_sub)}",
        f"  {T('dr_searchers')}:   <b>{_n(t_uniq)}</b>",
        f"  {T('dr_with_results')}: <b>{_n(t_comp)}</b>{_delta(t_comp, y_comp)}",
        f"  {T('dr_no_results')}:  <b>{_n(t_no)}</b>",
        f"  {T('dr_errors')}:      <b>{_n(t_err)}</b>",
        "",
        f"<b>{T('dr_delivery')}</b>",
        f"  {T('dr_success')}: <b>{_n(t_sent)}</b>{_delta(t_sent, y_sent)}",
        f"  {T('dr_failed')}:  <b>{_n(t_fail)}</b>",
        "",
        f"<b>{T('dr_ext')}</b>",
        f"  {T('dr_total')}:       <b>{_n(today_x.get('total_submitted'))}</b>{_delta(today_x.get('total_submitted', 0), yest_x.get('total_submitted', 0))}",
        f"  {T('dr_searchers')}:   <b>{_n(today_x.get('unique_searchers'))}</b>",
        f"  {T('dr_with_results')}: <b>{_n(today_x.get('total_completed'))}</b>",
        f"  {T('dr_errors')}:      <b>{_n(today_x.get('total_errors'))}</b>",
        f"  {T('dr_ext_pdf')}:     <b>{_n(today_x.get('pdf_sent'))}</b>{_delta(today_x.get('pdf_sent', 0), yest_x.get('pdf_sent', 0))}",
        "",
        f"<i>{T('dr_delta_note')}</i>",
    ]
    return "\n".join(lines)


# part2: Keyboard سازنده‌ها


def analytics_main_keyboard(uid: int = 0) -> types.InlineKeyboardMarkup:
    T  = lambda k: _t(uid, k)
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton(T("btn_overview"),   callback_data=f"{PREFIX}:overview"),
        types.InlineKeyboardButton(T("btn_users"),      callback_data=f"{PREFIX}:users:all"),
    )
    mk.add(
        types.InlineKeyboardButton(T("btn_search"),     callback_data=f"{PREFIX}:search:all"),
        types.InlineKeyboardButton(T("btn_delivery"),   callback_data=f"{PREFIX}:delivery:all"),
    )
    mk.add(
        types.InlineKeyboardButton(T("btn_external"),   callback_data=f"{PREFIX}:ext:all"),
    )
    mk.add(
        types.InlineKeyboardButton(T("btn_retention"),  callback_data=f"{PREFIX}:retention"),
        types.InlineKeyboardButton(T("btn_growth"),     callback_data=f"{PREFIX}:growth:day"),
    )
    mk.add(
        types.InlineKeyboardButton(T("btn_today_report"), callback_data=f"{PREFIX}:today_report"),
    )
    mk.add(
        types.InlineKeyboardButton(T("btn_back_panel"), callback_data="adm_open_panel"),
    )
    return mk


def period_filter_keyboard(section: str, current: str = "all", uid: int = 0) -> types.InlineKeyboardMarkup:
    """کیبورد انتخاب بازه زمانی برای هر بخش."""
    T  = lambda k: _t(uid, k)
    mk = types.InlineKeyboardMarkup(row_width=3)
    periods = [
        ("today",   T("btn_today")),
        ("week",    T("btn_7d")),
        ("month",   T("btn_30d")),
        ("quarter", T("btn_90d")),
        ("all",     T("btn_all")),
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
        types.InlineKeyboardButton(T("btn_refresh"),   callback_data=f"{PREFIX}:{section}:{current}"),
        types.InlineKeyboardButton(T("btn_dashboard"), callback_data=f"{PREFIX}:main"),
    )
    return mk


def growth_period_keyboard(current: str = "day", uid: int = 0) -> types.InlineKeyboardMarkup:
    T  = lambda k: _t(uid, k)
    mk = types.InlineKeyboardMarkup(row_width=3)
    periods = [
        ("day",   T("growth_day")),
        ("week",  T("growth_week")),
        ("month", T("growth_month")),
    ]
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
        types.InlineKeyboardButton(T("btn_refresh"),   callback_data=f"{PREFIX}:growth:{current}"),
        types.InlineKeyboardButton(T("btn_dashboard"), callback_data=f"{PREFIX}:main"),
    )
    return mk


def back_to_main_keyboard(uid: int = 0) -> types.InlineKeyboardMarkup:
    T  = lambda k: _t(uid, k)
    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton(T("btn_refresh"),   callback_data=f"{PREFIX}:overview"),
        types.InlineKeyboardButton(T("btn_dashboard"), callback_data=f"{PREFIX}:main"),
    )
    return mk


# ═══════════════════════════════════════════════════════════════════════════════
# بخش سوم: Entry points (فراخوانی از admin.py)
# ═══════════════════════════════════════════════════════════════════════════════

def show_analytics_main(bot, chat_id: int, uid: int = 0):
    """نمایش منوی اصلی داشبورد analytics."""
    bot.send_message(
        chat_id,
        f"<b>{_t(uid, 'dashboard_title')}</b>\n{_t(uid, 'dashboard_prompt')}",
        reply_markup=analytics_main_keyboard(uid),
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
            _edit_or_send(bot, callback,
                          f"<b>{_t(uid, 'dashboard_title')}</b>\n{_t(uid, 'dashboard_prompt')}",
                          analytics_main_keyboard(uid))

        elif section == "overview":
            text = _build_overview_text(uid)
            _edit_or_send(bot, callback, text, back_to_main_keyboard(uid))

        elif section == "users":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_user_stats_text(period, uid)
            _edit_or_send(bot, callback, text, period_filter_keyboard("users", period, uid))

        elif section == "search":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_search_stats_text(period, uid)
            _edit_or_send(bot, callback, text, period_filter_keyboard("search", period, uid))

        elif section == "delivery":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_delivery_stats_text(period, uid)
            _edit_or_send(bot, callback, text, period_filter_keyboard("delivery", period, uid))

        elif section == "ext":
            period = parts[1] if len(parts) > 1 else "all"
            text   = _build_external_stats_text(period, uid)
            _edit_or_send(bot, callback, text, period_filter_keyboard("ext", period, uid))

        elif section == "retention":
            text = _build_retention_text(uid)
            _edit_or_send(bot, callback, text, back_to_main_keyboard(uid))

        elif section == "growth":
            period = parts[1] if len(parts) > 1 else "day"
            text   = _build_growth_text(period, uid)
            _edit_or_send(bot, callback, text, growth_period_keyboard(period, uid))

        elif section == "today_report":
            send_report_to_admin(bot, uid)

        else:
            return False

        return True

    except Exception as exc:
        logger.error("[dashboard] handle_analytics_callback error (data=%s): %s", data, exc)
        try:
            bot.answer_callback_query(callback.id, _t(uid, "error_occurred"))
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
    except Exception as exc:
        # «Refresh» روی داده‌ی بدون تغییر → پیام جدید نفرست
        if "message is not modified" in str(exc).lower():
            return
        # پیام قابل edit نیست (خیلی قدیمی یا حذف‌شده)
        bot.send_message(
            callback.message.chat.id,
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# بخش چهارم: گزارش روزانه خودکار
# ═══════════════════════════════════════════════════════════════════════════════


def send_report_to_admin(bot, uid: int) -> None:
    """
    ارسال گزارش امروز به یک ادمین مشخص — on-demand، بدون scheduler.
    از داشبورد analytics با کلیک دکمه «گزارش امروز» فراخوانی می‌شود.
    """
    report_date = _report_date_for_now()
    text = _build_daily_report_text(report_date, uid=uid)
    try:
        bot.send_message(uid, text, parse_mode="HTML")
        logger.info("[dashboard] On-demand report sent to admin %s for %s.", uid, report_date)
    except Exception as exc:
        logger.error("[dashboard] Failed to send on-demand report to admin %s: %s", uid, exc)
