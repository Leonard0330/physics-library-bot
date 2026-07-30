"""database.py"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "physics_library.db")


# Seed data for physics_fields table (populated once during init_db)
_PHYSICS_FIELDS_SEED = {
    "classical_mechanics":        ("مکانیک کلاسیک", "Classical Mechanics",        "CM"),
    "electromagnetism":           ("الکترومغناطیس", "Electromagnetism",           "EM"),
    "modern_physics":             ("فیزیک مدرن", "Modern Physics",                "MOD"),
    "general_physics":            ("فیزیک پایه", "General Physics",               "GEN"),
    "quantum_mechanics":          ("مکانیک کوانتومی (و نظریه میدان)", "Quantum Mechanics (incl. QFT)", "QM"),
    "relativity":                 ("نسبیت", "Relativity",                          "REL"),
    "thermodynamics_statistical": ("ترمودینامیک و مکانیک آماری", "Thermodynamics & Statistical Mechanics", "THS"),
    "mathematical_physics":       ("فیزیک ریاضی", "Mathematical Physics",         "MTH"),
    "condensed_matter":           ("فیزیک ماده چگال (حالت جامد، نرم، نانو، مواد)", "Condensed Matter Physics (Solid, Soft, Nano, Materials)", "CMP"),
    "optics_amo":                 ("اپتیک، لیزر و فیزیک اتمی-مولکولی (AMO)", "Optics, Laser & AMO Physics", "OPT"),
    "nuclear_particle_plasma":    ("فیزیک هسته‌ای، ذرات و پلاسما", "Nuclear, Particle & Plasma Physics", "NPP"),
    "astrophysics_cosmology":     ("اخترفیزیک، کیهان‌شناسی و نجوم", "Astrophysics, Cosmology & Astronomy", "AST"),
    "computational_nonlinear":    ("فیزیک محاسباتی و دینامیک غیرخطی", "Computational Physics & Nonlinear Dynamics", "CMN"),
    "biophysics_medical":         ("بیوفیزیک و فیزیک پزشکی", "Biophysics & Medical Physics",    "BIO"),
    "chemical_acoustics":         ("فیزیک شیمی و آکوستیک", "Chemical Physics & Acoustics",      "CHE"),
    "other":                      ("سایر / میان‌رشته‌ای", "Other / Interdisciplinary",          "OTH"),
}

# In-memory caches — loaded from DB by _load_field_caches() after init_db()
PHYSICS_FIELDS: dict[str, tuple[str, str]] = {}
FIELD_CODES:    dict[str, str]             = {}


def _load_field_caches(conn: Optional[sqlite3.Connection] = None) -> None:
    """Reload PHYSICS_FIELDS and FIELD_CODES from the physics_fields table.

    Pass an existing *conn* to reuse the current transaction (e.g. during
    init_db); omit it to open a fresh connection.
    """
    global PHYSICS_FIELDS, FIELD_CODES

    def _fetch(c: sqlite3.Connection) -> None:
        global PHYSICS_FIELDS, FIELD_CODES
        rows = c.execute(
            "SELECT key, name_fa, name_en, code FROM physics_fields"
        ).fetchall()
        PHYSICS_FIELDS = {r["key"]: (r["name_fa"], r["name_en"]) for r in rows}
        FIELD_CODES    = {r["key"]: r["code"]                    for r in rows}

    if conn is not None:
        _fetch(conn)
    else:
        with get_connection() as c:
            _fetch(c)


def field_code(physics_field: str) -> str:
    if physics_field in FIELD_CODES:
        return FIELD_CODES[physics_field]
    return (physics_field or "gen")[:3].upper()


def get_display_id(resource: "sqlite3.Row | dict") -> str:
    """Return the display ID for a resource.

    Books  → #QM-B7  (field_code + 'B' prefix + field_number)
    Articles → #QM-A3 (field_code + 'A' prefix + field_number)
    Unknown resource_type falls back to book behaviour.
    """
    keys = resource.keys() if hasattr(resource, "keys") else resource
    field  = resource["physics_field"]
    number = resource["field_number"] if "field_number" in keys else None

    if number:
        rtype = resource["resource_type"] if "resource_type" in keys else "book"
        if rtype == "article":
            return f"#{field_code(field)}-A{number}"
        return f"#{field_code(field)}-B{number}"
    return f"#{resource['id']}"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row         
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  
    return conn


def init_db() -> None:
    with get_connection() as conn:

        # ── physics_fields table ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS physics_fields (
                key         TEXT PRIMARY KEY,
                name_fa     TEXT NOT NULL,
                name_en     TEXT NOT NULL,
                code        TEXT NOT NULL UNIQUE,
                parent_key  TEXT REFERENCES physics_fields(key) ON DELETE SET NULL
            )
        """)

        # Seed rows that are missing (idempotent)
        for key, (name_fa, name_en, code) in _PHYSICS_FIELDS_SEED.items():
            conn.execute("""
                INSERT INTO physics_fields (key, name_fa, name_en, code, parent_key)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(key) DO NOTHING
            """, (key, name_fa, name_en, code))

        # ── books table ──────────────────────────────────────────────────────
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

        # ── New columns on books (article support + resource_type) ────────────
        book_cols = {row["name"] for row in conn.execute("PRAGMA table_info(books)")}
        _new_book_cols = [
            ("resource_type",    "TEXT NOT NULL DEFAULT 'book'"),
            ("doi",              "TEXT"),
            ("journal",          "TEXT"),
            ("volume",           "TEXT"),
            ("issue",            "TEXT"),
            ("pages",            "TEXT"),
            ("url",              "TEXT"),
            ("publication_date", "TEXT"),
        ]
        for col_name, col_def in _new_book_cols:
            if col_name not in book_cols:
                conn.execute(f"ALTER TABLE books ADD COLUMN {col_name} {col_def}")

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
        _load_field_caches(conn)
    print(f"[DB] دیتابیس آماده شد: {DB_PATH}")


# ── Generic resource functions ───────────────────────────────────────────────

def add_resource(
    title: str,
    author: str,
    language: str,
    physics_field: str,
    resource_type: str = "book",
    file_id: str = "",
    file_name: str = "",
    file_size: int = 0,
    description: str = "",
    edition: str = "",
    year: Optional[int] = None,
    cover_file_id: str = "",
    added_by: Optional[int] = None,
    # article-specific fields
    doi: str = "",
    journal: str = "",
    volume: str = "",
    issue: str = "",
    pages: str = "",
    url: str = "",
    publication_date: str = "",
) -> int:
    if physics_field not in PHYSICS_FIELDS:
        raise ValueError(f"فیلد نامعتبر: {physics_field}")
    if language not in ("fa", "en"):
        raise ValueError("زبان باید 'fa' یا 'en' باشد")
    if resource_type not in ("book", "article"):
        raise ValueError("resource_type باید 'book' یا 'article' باشد")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(field_number), 0) AS mx FROM books "
            "WHERE physics_field = ? AND resource_type = ?",
            (physics_field, resource_type)
        ).fetchone()
        next_number = row["mx"] + 1

        cur = conn.execute("""
            INSERT INTO books
                (title, author, language, physics_field,
                 description, edition, year,
                 file_id, file_name, file_size,
                 cover_file_id, added_by, field_number, resource_type,
                 doi, journal, volume, issue, pages, url, publication_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            title, author, language, physics_field,
            description, edition, year,
            file_id, file_name, file_size,
            cover_file_id, added_by, next_number, resource_type,
            doi, journal, volume, issue, pages, url, publication_date,
        ))
        conn.commit()
        return cur.lastrowid


def get_resource(resource_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE id = ?", (resource_id,)
        ).fetchone()


def search_resources(
    query: str = "",
    physics_field: str = "",
    language: str = "",
    resource_type: str = "",
    limit: int = 10,
    offset: int = 0,
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    params: list = []

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

    if resource_type:
        conditions.append("resource_type = ?")
        params.append(resource_type)

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


def find_resource_by_display_id(text: str) -> Optional[sqlite3.Row]:
    """Parse a display ID like #QM-B7 or #QM-A3 and return the matching row.

    Format: #{FIELD_CODE}-{TYPE_PREFIX}{NUMBER}
      TYPE_PREFIX B → book, A → article (omitting prefix also matches books).
    """
    cleaned = text.strip().lstrip("#").upper().replace(" ", "")
    # Split on '-' to separate field code from the rest
    parts = cleaned.split("-", 1)
    if len(parts) != 2:
        return None

    letters = parts[0]                    # e.g. "QM"
    rest    = parts[1]                    # e.g. "B7" / "A3" / "7"

    if not letters or not rest:
        return None

    # Detect optional type prefix
    if rest and rest[0] in ("B", "A"):
        type_prefix = rest[0]
        digits      = rest[1:]
    else:
        type_prefix = "B"
        digits      = rest

    if not digits.isdigit():
        return None

    resource_type  = "article" if type_prefix == "A" else "book"
    field_number   = int(digits)

    matching_fields = [f for f, code in FIELD_CODES.items() if code == letters]
    if not matching_fields:
        return None

    with get_connection() as conn:
        for field in matching_fields:
            row = conn.execute(
                "SELECT * FROM books WHERE physics_field = ? AND field_number = ? AND resource_type = ?",
                (field, field_number, resource_type)
            ).fetchone()
            if row:
                return row
    return None


# ── Book CRUD (thin wrappers around generic functions) ───────────────────────

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
    return add_resource(
        title=title,
        author=author,
        language=language,
        physics_field=physics_field,
        resource_type="book",
        file_id=file_id,
        file_name=file_name,
        file_size=file_size,
        description=description,
        edition=edition,
        year=year,
        cover_file_id=cover_file_id,
        added_by=added_by,
    )


def get_book(book_id: int) -> Optional[sqlite3.Row]:
    return get_resource(book_id)


def find_book_by_display_id(text: str) -> Optional[sqlite3.Row]:
    # Legacy: treat bare codes (no B/A prefix) as books, matching old behaviour
    cleaned = text.strip().lstrip("#").upper().replace(" ", "")
    # If caller passes old-style "#QM-7" without prefix, normalise to "#QM-B7"
    parts = cleaned.split("-", 1)
    if len(parts) == 2 and parts[1] and parts[1][0] not in ("B", "A"):
        cleaned = f"{parts[0]}-B{parts[1]}"
    return find_resource_by_display_id("#" + cleaned)


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
    return search_resources(
        query=query,
        physics_field=physics_field,
        language=language,
        resource_type="book",
        limit=limit,
        offset=offset,
    )


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
        total_books    = conn.execute(
            "SELECT COUNT(*) FROM books WHERE resource_type = 'book'"
        ).fetchone()[0]
        total_articles = conn.execute(
            "SELECT COUNT(*) FROM books WHERE resource_type = 'article'"
        ).fetchone()[0]
        total_resources = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        total_fa       = conn.execute("SELECT COUNT(*) FROM books WHERE language='fa'").fetchone()[0]
        total_en       = conn.execute("SELECT COUNT(*) FROM books WHERE language='en'").fetchone()[0]
        total_dl       = conn.execute("SELECT COALESCE(SUM(download_count),0) FROM books").fetchone()[0]
        unique_fields  = conn.execute("SELECT COUNT(DISTINCT physics_field) FROM books").fetchone()[0]
    return {
        "total_books":      total_books,
        "total_articles":   total_articles,
        "total_resources":  total_resources,
        "fa_books":         total_fa,
        "en_books":         total_en,
        "total_downloads":  total_dl,
        "unique_fields":    unique_fields,
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
    import tempfile, os as _os
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    DB_PATH = _tmp.name
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
    _os.unlink(_tmp.name)