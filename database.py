"""
database.py — ماژول دیتابیس کتابخانه فیزیک
SQLite با پشتیبانی از:
  - CRUD کتاب
  - فیلدهای فیزیکی (کوانتوم، الکترومغناطیس، ...)
  - آپلود فایل (ذخیره file_id تلگرام)
  - آمار دانلود
  - سرچ پیشرفته
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "physics_library.db")

# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

PHYSICS_FIELDS = {
    "classical_mechanics":   ("مکانیک کلاسیک", "Classical Mechanics"),
    "electromagnetism":      ("الکترومغناطیس", "Electromagnetism"),
    "quantum_mechanics":     ("مکانیک کوانتومی", "Quantum Mechanics"),
    "quantum_field_theory":  ("نظریه میدان کوانتومی", "Quantum Field Theory"),
    "relativity":            ("نسبیت", "Relativity"),
    "thermodynamics":        ("ترمودینامیک", "Thermodynamics"),
    "statistical_mechanics": ("مکانیک آماری", "Statistical Mechanics"),
    "mathematical_physics":  ("فیزیک ریاضی", "Mathematical Physics"),

    "condensed_matter":      ("فیزیک ماده چگال", "Condensed Matter Physics"),
    "solid_state":           ("فیزیک حالت جامد", "Solid State Physics"),
    "soft_matter":           ("فیزیک ماده نرم", "Soft Matter Physics"),
    "nanophysics":           ("نانوفیزیک", "Nanophysics"),
    "materials_physics":     ("فیزیک مواد", "Materials Physics"),

    "optics_laser":          ("اپتیک و لیزر", "Optics & Laser Physics"),
    "amo_physics":           ("فیزیک اتمی، مولکولی و اپتیک (AMO)", "Atomic, Molecular and Optical Physics (AMO)"),
    "plasma_physics":        ("فیزیک پلاسما", "Plasma Physics"),
    "nuclear_physics":       ("فیزیک هسته‌ای", "Nuclear Physics"),
    "particle_physics":      ("فیزیک ذرات", "Particle Physics"),

    "astrophysics":          ("اخترفیزیک", "Astrophysics"),
    "cosmology":             ("کیهان‌شناسی", "Cosmology"),
    "astronomy":             ("نجوم", "Astronomy"),
    "geophysics":            ("ژئوفیزیک", "Geophysics"),

    "computational_physics": ("فیزیک محاسباتی", "Computational Physics"),
    "nonlinear_dynamics":    ("دینامیک غیرخطی و آشوب", "Nonlinear Dynamics & Chaos"),

    "biophysics":            ("بیوفیزیک", "Biophysics"),
    "medical_physics":       ("فیزیک پزشکی", "Medical Physics"),
    "chemical_physics":      ("فیزیک شیمی", "Chemical Physics"),

    "acoustics":             ("آکوستیک", "Acoustics"),

    "other":                 ("سایر / میان‌رشته‌ای", "Other / Interdisciplinary"),
}


# ─────────────────────────────────────────────
# اتصال و ساخت جداول
# ─────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # دسترسی به ستون‌ها با نام
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # بهتر برای multi-access
    return conn


def init_db() -> None:
    """ساخت تمام جداول (اگر وجود نداشته باشند)."""
    with get_connection() as conn:

        # ── جدول کتاب‌ها ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                author          TEXT    NOT NULL,
                language        TEXT    NOT NULL CHECK(language IN ('fa', 'en')),
                physics_field   TEXT    NOT NULL,
                description     TEXT,
                edition         TEXT,
                year            INTEGER,
                file_id         TEXT,        -- file_id فایل تلگرام
                file_name       TEXT,        -- نام فایل اصلی
                file_size       INTEGER,     -- بایت
                cover_file_id   TEXT,        -- عکس جلد (اختیاری)
                added_by        INTEGER,     -- telegram user_id ادمین
                download_count  INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── فهرست‌سازی برای سرچ سریع ──────────────────────────────────────
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_books_field
            ON books(physics_field)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_books_language
            ON books(language)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_books_title
            ON books(title)
        """)

        # ── جدول لاگ دانلود ───────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS download_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL,
                downloaded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dl_book
            ON download_logs(book_id)
        """)

        # ── جدول کاربران (اختیاری، برای آمار) ───────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
                is_admin    INTEGER NOT NULL DEFAULT 0,
                lang        TEXT
            )
        """)

        # ── مهاجرت برای دیتابیس‌های قدیمی‌تر که ستون lang رو ندارن ──────
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "lang" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")

        conn.commit()
    print(f"[DB] دیتابیس آماده شد: {DB_PATH}")


# ─────────────────────────────────────────────
# CRUD کتاب
# ─────────────────────────────────────────────

def add_book(
    title: str,
    author: str,
    language: str,           # 'fa' یا 'en'
    physics_field: str,
    file_id: str,
    file_name: str,
    file_size: int,
    description: str = "",
    edition: str = "",
    year: Optional[int] = None,
    cover_file_id: str = "",
    added_by: Optional[int] = None,
) -> int:
    """اضافه کردن کتاب جدید — برمی‌گردونه id کتاب."""
    if physics_field not in PHYSICS_FIELDS:
        raise ValueError(f"فیلد نامعتبر: {physics_field}")
    if language not in ("fa", "en"):
        raise ValueError("زبان باید 'fa' یا 'en' باشد")

    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO books
                (title, author, language, physics_field,
                 description, edition, year,
                 file_id, file_name, file_size,
                 cover_file_id, added_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            title, author, language, physics_field,
            description, edition, year,
            file_id, file_name, file_size,
            cover_file_id, added_by,
        ))
        conn.commit()
        return cur.lastrowid


def get_book(book_id: int) -> Optional[sqlite3.Row]:
    """گرفتن یک کتاب با id."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()


def update_book(book_id: int, **kwargs) -> bool:
    """آپدیت هر فیلدی از کتاب.
    مثال: update_book(3, title='نام جدید', year=2024)
    """
    allowed = {
        "title", "author", "language", "physics_field",
        "description", "edition", "year",
        "file_id", "file_name", "file_size", "cover_file_id",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [book_id]

    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE books SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0


def delete_book(book_id: int) -> bool:
    """حذف کتاب (لاگ دانلود هم cascade حذف می‌شه)."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()
        return cur.rowcount > 0


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

def search_books(
    query: str = "",
    physics_field: str = "",
    language: str = "",
    limit: int = 10,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """
    سرچ ترکیبی:
      - query     → جستجو در عنوان و نام نویسنده
      - physics_field → فیلد فیزیکی
      - language  → 'fa' یا 'en'
    """
    conditions = []
    params = []

    if query:
        conditions.append("(title LIKE ? OR author LIKE ?)")
        like = f"%{query}%"
        params += [like, like]

    if physics_field:
        conditions.append("physics_field = ?")
        params.append(physics_field)

    if language:
        conditions.append("language = ?")
        params.append(language)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT * FROM books
        {where}
        ORDER BY download_count DESC, created_at DESC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    with get_connection() as conn:
        return conn.execute(sql, params).fetchall()


def get_books_by_field(physics_field: str, limit: int = 20) -> list[sqlite3.Row]:
    """همه کتاب‌های یک فیلد خاص."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE physics_field = ? ORDER BY title LIMIT ?",
            (physics_field, limit)
        ).fetchall()


def list_all_fields() -> list[str]:
    """فیلدهایی که حداقل یک کتاب دارند."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT physics_field, COUNT(*) as cnt "
            "FROM books GROUP BY physics_field ORDER BY cnt DESC"
        ).fetchall()
    return [r["physics_field"] for r in rows]


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

def record_download(book_id: int, user_id: int) -> None:
    """ثبت یک دانلود + افزایش شمارنده."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO download_logs (book_id, user_id) VALUES (?,?)",
            (book_id, user_id)
        )
        conn.execute(
            "UPDATE books SET download_count = download_count + 1 WHERE id = ?",
            (book_id,)
        )
        conn.commit()


def get_top_downloads(limit: int = 10) -> list[sqlite3.Row]:
    """پرطرفدارترین کتاب‌ها."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, title, author, physics_field, download_count "
            "FROM books ORDER BY download_count DESC LIMIT ?",
            (limit,)
        ).fetchall()


def get_book_stats(book_id: int) -> dict:
    """آمار کامل یک کتاب: دانلود کل + دانلود ۳۰ روز اخیر."""
    with get_connection() as conn:
        book = conn.execute(
            "SELECT download_count FROM books WHERE id = ?", (book_id,)
        ).fetchone()

        recent = conn.execute(
            """SELECT COUNT(*) as cnt FROM download_logs
               WHERE book_id = ?
               AND downloaded_at >= datetime('now', '-30 days')""",
            (book_id,)
        ).fetchone()

    return {
        "total": book["download_count"] if book else 0,
        "last_30_days": recent["cnt"] if recent else 0,
    }


def get_library_stats() -> dict:
    """آمار کلی کتابخانه."""
    with get_connection() as conn:
        total_books   = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        total_fa      = conn.execute("SELECT COUNT(*) FROM books WHERE language='fa'").fetchone()[0]
        total_en      = conn.execute("SELECT COUNT(*) FROM books WHERE language='en'").fetchone()[0]
        total_dl      = conn.execute("SELECT COALESCE(SUM(download_count),0) FROM books").fetchone()[0]
        unique_fields = conn.execute("SELECT COUNT(DISTINCT physics_field) FROM books").fetchone()[0]
    return {
        "total_books": total_books,
        "fa_books": total_fa,
        "en_books": total_en,
        "total_downloads": total_dl,
        "unique_fields": unique_fields,
    }


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────

def upsert_user(user_id: int, username: str = "", first_name: str = "") -> None:
    """ثبت یا آپدیت کاربر."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_seen)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name,
                last_seen  = datetime('now')
        """, (user_id, username, first_name))
        conn.commit()


def set_admin(user_id: int, is_admin: bool = True) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_admin = ? WHERE user_id = ?",
            (1 if is_admin else 0, user_id)
        )
        conn.commit()


def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["is_admin"])


def get_user_lang(user_id: int) -> Optional[str]:
    """زبان ذخیره‌شدهٔ کاربر رو برمی‌گردونه ('fa' یا 'en')، یا None اگر هنوز تعیین نشده."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT lang FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["lang"] if row and row["lang"] else None


def set_user_lang(user_id: int, lang: str) -> None:
    """ذخیرهٔ زبان انتخابی کاربر (پرسیستنت، نه فقط در حافظه)."""
    if lang not in ("fa", "en"):
        return
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (user_id, lang, last_seen)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang
        """, (user_id, lang))
        conn.commit()


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()

    bid = add_book(
        title="Principles of Quantum Mechanics",
        author="R. Shankar",
        language="en",
        physics_field="quantum_mechanics",
        file_id="BQACAgIAAxkBAAIBhGV...",   # یه file_id نمونه
        file_name="shankar_qm.pdf",
        file_size=15_000_000,
        description="یکی از بهترین رفرنس‌های مکانیک کوانتومی",
        edition="2nd",
        year=1994,
        added_by=123456789,
    )
    print(f"[+] کتاب اضافه شد | id={bid}")

    bid2 = add_book(
        title="مکانیک کوانتومی",
        author="گریفیتس | ترجمه: احمدی",
        language="fa",
        physics_field="quantum_mechanics",
        file_id="BQACAgIAAxkBAAIBhGW...",
        file_name="griffiths_fa.pdf",
        file_size=12_000_000,
    )
    print(f"[+] کتاب فارسی اضافه شد | id={bid2}")

    results = search_books(query="Quantum")
    print(f"\n[سرچ 'Quantum'] → {len(results)} نتیجه")
    for r in results:
        print(f"  • {r['title']} | {r['author']} | {r['language']}")

    results2 = search_books(physics_field="quantum_mechanics", language="fa")
    print(f"\n[فیلتر: quantum_mechanics + فارسی] → {len(results2)} نتیجه")

    record_download(bid, user_id=111)
    record_download(bid, user_id=222)
    record_download(bid2, user_id=111)

    print(f"\n[آمار کتاب {bid}]:", get_book_stats(bid))

    stats = get_library_stats()
    print(f"\n[آمار کلی] {stats}")

    upsert_user(123456789, username="admin_user", first_name="علی")
    set_admin(123456789, True)
    print(f"\n[ادمین؟] {is_admin(123456789)}")

    print("\n✅ همه تست‌ها با موفقیت اجرا شدند.")
