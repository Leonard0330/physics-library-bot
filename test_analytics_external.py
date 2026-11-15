"""تست شمارش رویدادهای ext_* در analytics (دیتابیس موقت، بدون نیاز به ربات)."""
import os, sys, tempfile, time, types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

import database
database.DB_PATH = os.environ["DB_PATH"]
with database.get_connection() as c:
    c.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, created_at TEXT, last_seen TEXT, language TEXT)")
    database._ensure_analytics_schema(c)
    c.execute("INSERT INTO users(user_id, created_at, last_seen) VALUES (1, datetime('now','-3 days'), datetime('now'))")
    c.commit()

import analytics


def _drain():
    t0 = time.time()
    while not analytics._event_queue.empty() and time.time() - t0 < 5:
        time.sleep(0.05)
    time.sleep(0.2)


def test_ext_events_counted():
    # دقیقاً همان wiring که در bot.py هست
    hook = lambda ev, uid, extra=None: analytics.log_event(ev, user_id=uid, extra=extra)
    hook("ext_search_submitted", 1, {"field": "keyword"})
    hook("ext_search_completed", 1, {"field": "keyword", "result_count": 5})
    hook("ext_details_viewed", 1, {"source": "arxiv"})
    hook("ext_pdf_requested", 1, {"source": "arxiv"})
    hook("ext_pdf_sent", 1, {"source": "arxiv"})
    _drain()

    s = analytics.get_external_search_stats_in_period()
    assert s["total_submitted"] == 1 and s["total_completed"] == 1
    assert s["pdf_requested"] == 1 and s["pdf_sent"] == 1 and s["details_viewed"] == 1

    # جستجوی محلی نباید تغییر کند
    assert analytics.get_search_stats()["total_submitted"] == 0
    assert analytics.get_delivery_stats()["total_sent"] == 0

    day = analytics.get_daily_activity(7)[-1]
    assert day["ext_searches"] == 1 and day["ext_pdfs"] == 1
    assert day["searches"] == 0 and day["deliveries"] == 0

    assert analytics.get_user_stats()["users_searched_external"] == 1
    assert analytics.get_summary_report()["external"]["total_submitted"] == 1


def test_dashboard_texts_include_ext():
    import analytics_dashboard as dash
    dash._t = lambda uid, key: dash.STRINGS.get(key, {}).get("en", key)
    assert "External Search" in dash._build_overview_text(1)
    assert "External Search" in dash._build_daily_report_text("2026-11-15", 1)
    assert "External search (unique users)" in dash._build_user_stats_text("all", 1)


if __name__ == "__main__":
    test_ext_events_counted()
    test_dashboard_texts_include_ext()
    print("OK")
