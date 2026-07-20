"""database.py"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "physics_library.db")


# Physics Fields
PHYSICS_FIELDS = {
    "classical_mechanics":        ("مکانیک کلاسیک", "Classical Mechanics"),
    "electromagnetism":           ("الکترومغناطیس", "Electromagnetism"),
    "modern_physics":             ("فیزیک مدرن", "Modern Physics"),
    "general_physics":            ("فیزیک پایه", "General Physics"),
    "quantum_mechanics":          ("مکانیک کوانتومی (و نظریه میدان)", "Quantum Mechanics (incl. QFT)"),
    "relativity":                 ("نسبیت", "Relativity"),
    "thermodynamics_statistical": ("ترمودینامیک و مکانیک آماری", "Thermodynamics & Statistical Mechanics"),
    "mathematical_physics":       ("فیزیک ریاضی", "Mathematical Physics"),

    "condensed_matter":           ("فیزیک ماده چگال (حالت جامد، نرم، نانو، مواد)", "Condensed Matter Physics (Solid, Soft, Nano, Materials)"),
    "optics_amo":                 ("اپتیک، لیزر و فیزیک اتمی-مولکولی (AMO)", "Optics, Laser & AMO Physics"),
    "nuclear_particle_plasma":    ("فیزیک هسته‌ای، ذرات و پلاسما", "Nuclear, Particle & Plasma Physics"),
    "astrophysics_cosmology":     ("اخترفیزیک، کیهان‌شناسی و نجوم", "Astrophysics, Cosmology & Astronomy"),
    "computational_nonlinear":    ("فیزیک محاسباتی و دینامیک غیرخطی", "Computational Physics & Nonlinear Dynamics"),
    "biophysics_medical":         ("بیوفیزیک و فیزیک پزشکی", "Biophysics & Medical Physics"),
    "chemical_acoustics":         ("فیزیک شیمی و آکوستیک", "Chemical Physics & Acoustics"),

    "other":                      ("سایر / میان‌رشته‌ای", "Other / Interdisciplinary"),
}

# Fields Code
FIELD_CODES = {
    "classical_mechanics":        "CM",
    "electromagnetism":           "EM",
    "modern_physics":             "MOD",
    "general_physics":            "GEN",
    "quantum_mechanics":          "QM",
    "relativity":                 "REL",
    "thermodynamics_statistical": "THS",
    "mathematical_physics":       "MTH",
    "condensed_matter":           "CMP",
    "optics_amo":                 "OPT",
    "nuclear_particle_plasma":    "NPP",
    "astrophysics_cosmology":     "AST",
    "computational_nonlinear":    "CMN",
    "biophysics_medical":         "BIO",
    "chemical_acoustics":         "CHE",
    "other":                      "OTH",
}


def field_code(physics_field: str) -> str:
    if physics_field in FIELD_CODES:
        return FIELD_CODES[physics_field]
    return (physics_field or "gen")[:3].upper()


def get_display_id(book: "sqlite3.Row | dict") -> str:
    field = book["physics_field"]
    number = book["field_number"] if ("field_number" in book.keys() if hasattr(book, "keys") else "field_number" in book) else None
    if number:
        return f"#{field_code(field)}-{number}"
    return f"#{book['id']}"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row         
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  
    return conn


def init_db() -> None:
    with get_connection() as conn:
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

        # CC for quick Search
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

        # Field Number
        book_cols = {row["name"] for row in conn.execute("PRAGMA table_info(books)")}
        if "field_number" not in book_cols:
            conn.execute("ALTER TABLE books ADD COLUMN field_number INTEGER")
            rows = conn.execute(
                "SELECT id, physics_field FROM books ORDER BY physics_field, created_at, id"
            ).fetchall()
            counters: dict[str, int] = {}
            for r in rows:
                counters[r["physics_field"]] = counters.get(r["physics_field"], 0) + 1
                conn.execute(
                    "UPDATE books SET field_number = ? WHERE id = ?",
                    (counters[r["physics_field"]], r["id"])
                )

        conn.execute(
            "UPDATE books SET physics_field = 'general_physics' "
            "WHERE physics_field = 'General Physics'"
        )

        # Download Logs
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

        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "lang" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")

        conn.commit()
    print(f"[DB] دیتابیس آماده شد: {DB_PATH}")


# Book CRUD

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
    if physics_field not in PHYSICS_FIELDS:
        raise ValueError(f"فیلد نامعتبر: {physics_field}")
    if language not in ("fa", "en"):
        raise ValueError("زبان باید 'fa' یا 'en' باشد")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(field_number), 0) AS mx FROM books WHERE physics_field = ?",
            (physics_field,)
        ).fetchone()
        next_number = row["mx"] + 1

        cur = conn.execute("""
            INSERT INTO books
                (title, author, language, physics_field,
                 description, edition, year,
                 file_id, file_name, file_size,
                 cover_file_id, added_by, field_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            title, author, language, physics_field,
            description, edition, year,
            file_id, file_name, file_size,
            cover_file_id, added_by, next_number,
        ))
        conn.commit()
        return cur.lastrowid


def get_book(book_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()


def find_book_by_display_id(text: str) -> Optional[sqlite3.Row]:
    cleaned = text.strip().lstrip("#").upper().replace(" ", "")
    letters = "".join(ch for ch in cleaned if ch.isalpha())
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not letters or not digits:
        return None

    matching_fields = [f for f, code in FIELD_CODES.items() if code == letters]
    if not matching_fields:
        return None

    with get_connection() as conn:
        for field in matching_fields:
            row = conn.execute(
                "SELECT * FROM books WHERE physics_field = ? AND field_number = ?",
                (field, int(digits))
            ).fetchone()
            if row:
                return row
    return None


def update_book(book_id: int, **kwargs) -> bool:
    allowed = {
        "title", "author", "language", "physics_field",
        "description", "edition", "year",
        "file_id", "file_name", "file_size", "cover_file_id",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    with get_connection() as conn:
        if "physics_field" in fields:
            current = conn.execute(
                "SELECT physics_field FROM books WHERE id = ?", (book_id,)
            ).fetchone()
            if current and current["physics_field"] != fields["physics_field"]:
                row = conn.execute(
                    "SELECT COALESCE(MAX(field_number), 0) AS mx FROM books WHERE physics_field = ?",
                    (fields["physics_field"],)
                ).fetchone()
                fields["field_number"] = row["mx"] + 1

        fields["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [book_id]

        cur = conn.execute(
            f"UPDATE books SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0


def delete_book(book_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.commit()
        return cur.rowcount > 0



# Advanced Search

def search_books(
    query: str = "",
    physics_field: str = "",
    language: str = "",
    limit: int = 10,
    offset: int = 0,
) -> list[sqlite3.Row]:
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
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE physics_field = ? ORDER BY title LIMIT ?",
            (physics_field, limit)
        ).fetchall()


def list_all_fields() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT physics_field, COUNT(*) as cnt "
            "FROM books GROUP BY physics_field ORDER BY cnt DESC"
        ).fetchall()
    return [r["physics_field"] for r in rows]


# Download Stats

def record_download(book_id: int, user_id: int) -> None:
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
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, title, author, physics_field, download_count "
            "FROM books ORDER BY download_count DESC LIMIT ?",
            (limit,)
        ).fetchall()


def get_book_stats(book_id: int) -> dict:
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


# user management

def upsert_user(user_id: int, username: str = "", first_name: str = "") -> None:
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
        conn.execute("""
            INSERT INTO users (user_id, is_admin, last_seen)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET is_admin = excluded.is_admin
        """, (user_id, 1 if is_admin else 0))
        conn.commit()


def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["is_admin"])


def list_admins() -> list[sqlite3.Row]:
    """لیست همهٔ ادمین‌های فعلی."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT user_id, username, first_name FROM users WHERE is_admin = 1 "
            "ORDER BY last_seen DESC"
        ).fetchall()


def get_user_lang(user_id: int) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT lang FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["lang"] if row and row["lang"] else None


def set_user_lang(user_id: int, lang: str) -> None:
    if lang not in ("fa", "en"):
        return
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (user_id, lang, last_seen)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang
        """, (user_id, lang))
        conn.commit()

# TEST
if __name__ == "__main__":
    init_db()

    bid = add_book(
        title="Principles of Quantum Mechanics",
        author="R. Shankar",
        language="en",
        physics_field="quantum_mechanics",
        file_id="BQACAgIAAxkBAAIBhGV...",  
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

    # search test
    results = search_books(query="Quantum")
    print(f"\n[سرچ 'Quantum'] → {len(results)} نتیجه")
    for r in results:
        print(f"  • {r['title']} | {r['author']} | {r['language']}")

    results2 = search_books(physics_field="quantum_mechanics", language="fa")
    print(f"\n[فیلتر: quantum_mechanics + فارسی] → {len(results2)} نتیجه")

    # download test
    record_download(bid, user_id=111)
    record_download(bid, user_id=222)
    record_download(bid2, user_id=111)

    print(f"\n[آمار کتاب {bid}]:", get_book_stats(bid))

    # stats test
    stats = get_library_stats()
    print(f"\n[آمار کلی] {stats}")

    # admin test
    upsert_user(123456789, username="admin_user", first_name="علی")
    set_admin(123456789, True)
    print(f"\n[ادمین؟] {is_admin(123456789)}")

    print("\n✅ همه تست‌ها با موفقیت اجرا شدند.")