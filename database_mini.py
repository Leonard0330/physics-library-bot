"""
database.py — Stub for test environment.

Implements the minimal API surface used by analytics.py, analytics_dashboard.py,
and test_dashboard.py:

  DB_PATH         — مسیر فایل SQLite
  get_connection  — context manager برای اتصال به دیتابیس
  init_db         — ایجاد جداول
  upsert_user     — ثبت یا بروزرسانی کاربر
  set_admin       — تنظیم وضعیت ادمین
  list_admins     — لیست ادمین‌های فعال
"""

import sqlite3
import contextlib
import os

DB_PATH: str = "test.db"


@contextlib.contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                is_admin   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                last_seen  TEXT
            );

            CREATE TABLE IF NOT EXISTS analytics_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT    NOT NULL,
                user_id     INTEGER,
                resource_id INTEGER,
                extra       TEXT,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_report_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT    NOT NULL,
                admin_id    INTEGER NOT NULL,
                status      TEXT    NOT NULL CHECK(status IN ('sent','failed','pending')),
                sent_at     TEXT    NOT NULL,
                UNIQUE(report_date, admin_id)
            );

            CREATE INDEX IF NOT EXISTS idx_report_log_date
                ON daily_report_log(report_date);
        """)
        conn.commit()


def upsert_user(user_id: int, username: str, full_name: str) -> bool:
    """
    ثبت کاربر جدید یا بروزرسانی last_seen.
    Returns True اگر کاربر جدید بود.
    """
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET last_seen=datetime('now'), username=?, full_name=? WHERE user_id=?",
                (username, full_name, user_id),
            )
            conn.commit()
            return False
        conn.execute(
            """INSERT INTO users (user_id, username, full_name, created_at, last_seen)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (user_id, username, full_name),
        )
        conn.commit()
        return True


def set_admin(user_id: int, is_admin: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_admin=? WHERE user_id=?",
            (1 if is_admin else 0, user_id),
        )
        conn.commit()


def list_admins() -> list:
    """لیست ادمین‌های فعال — هر row یک dict با کلید user_id."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, username, full_name FROM users WHERE is_admin=1"
        ).fetchall()
    return [dict(r) for r in rows]


def is_admin(user_id: int) -> bool:
    """آیا کاربر ادمین است؟"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row["is_admin"])


def get_user_lang(user_id: int) -> str:
    """زبان کاربر — در stub همیشه 'fa' برمی‌گردد."""
    return "fa"
