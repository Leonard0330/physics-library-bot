import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "physics_library.db")

_PHYSICS_FIELDS_SEED = {
    "classical_mechanics":        ("مکانیک کلاسیک", "Classical Mechanics",                                              "CM"),
    "electromagnetism":           ("الکترومغناطیس", "Electromagnetism",                                                "EM"),
    "general_physics":            ("فیزیک پایه", "General Physics",                                                    "GEN"),
    "quantum_mechanics":          ("مکانیک کوانتومی (و نظریه میدان)", "Quantum Mechanics (incl. QFT)",                "QM"),
    "relativity":                 ("نسبیت", "Relativity",                                                              "REL"),
    "thermodynamics_statistical": ("ترمودینامیک و مکانیک آماری", "Thermodynamics & Statistical Physics",             "THS"),
    "mathematical_physics":       ("فیزیک ریاضی", "Mathematical Physics",                                             "MTH"),
    "condensed_matter":           ("فیزیک ماده چگال (حالت جامد، نرم، نانو، مواد)", "Condensed Matter Physics (Solid, Soft, Nano, Materials)", "CMP"),
    "optics_amo":                 ("اپتیک، لیزر و فیزیک اتمی-مولکولی (AMO)", "Optics, Laser & AMO Physics",         "OPT"),
    "nuclear_physics":            ("فیزیک هسته‌ای", "Nuclear Physics",                                                "NUC"),
    "particle_physics":           ("فیزیک ذرات", "Particle Physics",                                                  "PAR"),
    "plasma_physics":             ("فیزیک پلاسما", "Plasma Physics",                                                  "PLA"),
    "astrophysics":               ("اخترفیزیک و نجوم", "Astrophysics & Astronomy",                                    "AST"),
    "cosmology":                  ("کیهان‌شناسی", "Cosmology",                                                        "COS"),
    "computational_nonlinear":    ("فیزیک محاسباتی و دینامیک غیرخطی", "Computational Physics & Nonlinear Dynamics",  "CMN"),
    "biophysics_medical":         ("بیوفیزیک و فیزیک پزشکی", "Biophysics & Medical Physics",                         "BIO"),
    "chemical_physics":           ("فیزیک شیمی", "Chemical Physics",                                                  "CHP"),
    "acoustics":                  ("آکوستیک", "Acoustics",                                                             "ACU"),
    "history_philosophy":         ("تاریخ و فلسفه فیزیک", "History & Philosophy of Physics",                         "HPP"),
    "other":                      ("سایر / میان‌رشته‌ای", "Other / Interdisciplinary",                               "OTH"),
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


def _try_load_field_caches_if_ready() -> None:
    """اگر دیتابیس از قبل وجود داشت و جدول physics_fields آماده بود، کش‌ها رو لود کن.
    این تابع هنگام import ماژول صدا زده می‌شه تا PHYSICS_FIELDS و FIELD_CODES
    بدون نیاز به فراخوانی init_db() در دسترس باشن (مثلاً بعد از ریستارت ربات).
    """
    try:
        if not os.path.exists(DB_PATH):
            return
        with get_connection() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "physics_fields" in tables:
                _load_field_caches(conn)
    except Exception:
        pass  


_try_load_field_caches_if_ready()


def init_db() -> None:
    with get_connection() as conn:

        # ── physics_fields table
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

        # ── books table 
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

        # ── New columns on books (article support + resource_type) 
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

        # ── bookmarks
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                user_id  INTEGER NOT NULL,
                book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                saved_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, book_id)
            )
        """)

        # ── field subscriptions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id       INTEGER NOT NULL,
                physics_field TEXT    NOT NULL,
                subscribed_at TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, physics_field)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                user_id    INTEGER NOT NULL,
                book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                rating     INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, book_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ratings_book ON ratings(book_id)")

        # ── pending_resources table (Phase 1: Foundation)
        # Stores metadata for resources awaiting admin file upload + publishing.
        # Intentionally has NO field_number — that is assigned only on publish via add_resource().
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_resources (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_type    TEXT    NOT NULL DEFAULT 'book'
                                         CHECK(resource_type IN ('book', 'article')),
                title            TEXT    NOT NULL,
                author           TEXT    NOT NULL,
                language         TEXT    NOT NULL CHECK(language IN ('fa', 'en')),
                physics_field    TEXT    NOT NULL,
                description      TEXT    NOT NULL DEFAULT '',
                edition          TEXT    NOT NULL DEFAULT '',
                year             INTEGER,
                doi              TEXT    NOT NULL DEFAULT '',
                journal          TEXT    NOT NULL DEFAULT '',
                volume           TEXT    NOT NULL DEFAULT '',
                issue            TEXT    NOT NULL DEFAULT '',
                pages            TEXT    NOT NULL DEFAULT '',
                url              TEXT    NOT NULL DEFAULT '',
                publication_date TEXT    NOT NULL DEFAULT '',
                file_id          TEXT,
                file_name        TEXT,
                file_size        INTEGER,
                status           TEXT    NOT NULL DEFAULT 'pending'
                                         CHECK(status IN ('pending', 'file_received', 'published', 'rejected')),
                created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_status
            ON pending_resources(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_field
            ON pending_resources(physics_field)
        """)

        conn.commit()
        _load_field_caches(conn)
    print(f"[DB] دیتابیس آماده شد: {DB_PATH}")


# ── Generic resource functions 

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
    order_by: str = "default",   # "default" | "recent" | "popular"
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    params: list = []

    # For ranking we need to know the words before building WHERE.
    words: list[str] = []

    if query:
        # Split query into words; require every word to appear in at least one
        # searchable field (AND across words, OR across fields per word).
        # "griffiths quantum" → title/author/... must match "griffiths" AND
        # title/author/... must match "quantum".
        # Single-word queries behave identically to the previous implementation.
        words = [w for w in query.split() if w]
        if not words:
            words = [query]
        for word in words:
            like = f"%{word}%"
            conditions.append(
                "(title LIKE ? OR author LIKE ? OR description LIKE ?"
                " OR doi LIKE ? OR journal LIKE ?)"
            )
            params += [like, like, like, like, like]

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

    if order_by == "recent":
        order_clause = "ORDER BY created_at DESC, id DESC"
        rank_params: list = []
    elif order_by == "popular":
        order_clause = "ORDER BY download_count DESC, created_at DESC"
        rank_params = []
    elif words:
        # Relevance ranking with a lightweight CASE expression:
        #   tier 0 — any search word appears in the title
        #   tier 1 — any search word appears in the author field
        #   tier 2 — match only in description / doi / journal
        # Within each tier results are sorted alphabetically.
        title_likes  = " OR ".join("title LIKE ?"  for _ in words)
        author_likes = " OR ".join("author LIKE ?" for _ in words)
        rank_params  = [f"%{w}%" for w in words] + [f"%{w}%" for w in words]
        order_clause = (
            f"ORDER BY "
            f"CASE WHEN ({title_likes})  THEN 0 "
            f"     WHEN ({author_likes}) THEN 1 "
            f"     ELSE 2 END ASC, "
            f"title ASC"
        )
    else:
        # default: alphabetical — neutral ordering for "all" lists
        order_clause = "ORDER BY title ASC"
        rank_params = []

    # rank_params go before WHERE params because ORDER BY is evaluated after
    # WHERE but SQLite needs the CASE literals in positional order.
    all_params = rank_params + params + [limit, offset]

    sql = f"""
        SELECT * FROM books
        {where}
        {order_clause}
        LIMIT ? OFFSET ?
    """

    with get_connection() as conn:
        return conn.execute(sql, all_params).fetchall()


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


# ── Book CRUD (thin wrappers around generic functions) 

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
        # shared fields
        "title", "author", "language", "physics_field",
        "description", "year",
        "file_id", "file_name", "file_size", "cover_file_id",
        # book-specific
        "edition",
        # article-specific
        "doi", "journal", "volume", "issue", "pages",
        "publication_date", "url",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    with get_connection() as conn:
        if "physics_field" in fields:
            current = conn.execute(
                "SELECT physics_field, resource_type FROM books WHERE id = ?", (book_id,)
            ).fetchone()
            if current and current["physics_field"] != fields["physics_field"]:
                row = conn.execute(
                    "SELECT COALESCE(MAX(field_number), 0) AS mx FROM books "
                    "WHERE physics_field = ? AND resource_type = ?",
                    (fields["physics_field"], current["resource_type"])
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
    order_by: str = "default",
) -> list[sqlite3.Row]:
    return search_resources(
        query=query,
        physics_field=physics_field,
        language=language,
        resource_type="book",
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def suggest_similar(query: str, limit: int = 3) -> list[sqlite3.Row]:
    words = [w.strip() for w in query.split() if len(w.strip()) >= 2]
    if not words:
        return []
    seen: set[int] = set()
    results: list[sqlite3.Row] = []
    with get_connection() as conn:
        for word in words:
            like = f"%{word}%"
            rows = conn.execute(
                "SELECT * FROM books WHERE title LIKE ? OR author LIKE ?"
                " OR description LIKE ? OR doi LIKE ? OR journal LIKE ? LIMIT ?",
                (like, like, like, like, like, limit)
            ).fetchall()
            for row in rows:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    results.append(row)
            if len(results) >= limit:
                break
    return results[:limit]


# ── Bookmarks ──────────────────────────────────────────────────────────────────

def toggle_bookmark(user_id: int, book_id: int) -> bool:
    """Toggle bookmark. Returns True if added, False if removed."""
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM bookmarks WHERE user_id=? AND book_id=?", (user_id, book_id)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM bookmarks WHERE user_id=? AND book_id=?", (user_id, book_id))
            conn.commit()
            return False
        conn.execute("INSERT INTO bookmarks (user_id, book_id) VALUES (?,?)", (user_id, book_id))
        conn.commit()
        return True


def get_bookmarks(user_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT b.* FROM books b JOIN bookmarks bm ON b.id=bm.book_id "
            "WHERE bm.user_id=? ORDER BY bm.saved_at DESC",
            (user_id,)
        ).fetchall()


def is_bookmarked(user_id: int, book_id: int) -> bool:
    with get_connection() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM bookmarks WHERE user_id=? AND book_id=?", (user_id, book_id)
        ).fetchone())


# ── Subscriptions ──────────────────────────────────────────────────────────────

def toggle_subscription(user_id: int, physics_field: str) -> bool:
    """Toggle field subscription. Returns True if subscribed, False if unsubscribed."""
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? AND physics_field=?",
            (user_id, physics_field)
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM subscriptions WHERE user_id=? AND physics_field=?",
                (user_id, physics_field)
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO subscriptions (user_id, physics_field) VALUES (?,?)",
            (user_id, physics_field)
        )
        conn.commit()
        return True


def get_field_subscribers(physics_field: str) -> list[int]:
    """Return list of user_ids subscribed to a field."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id FROM subscriptions WHERE physics_field=?", (physics_field,)
        ).fetchall()
    return [r["user_id"] for r in rows]


def get_user_subscriptions(user_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT physics_field FROM subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
    return [r["physics_field"] for r in rows]


def is_subscribed(user_id: int, physics_field: str) -> bool:
    with get_connection() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? AND physics_field=?",
            (user_id, physics_field)
        ).fetchone())


# ── Download History ───────────────────────────────────────────────────────────

def get_download_history(user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """Return distinct resources downloaded by user, most recent first."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT b.*, MAX(dl.downloaded_at) AS downloaded_at FROM books b "
            "JOIN download_logs dl ON b.id = dl.book_id "
            "WHERE dl.user_id = ? GROUP BY b.id "
            "ORDER BY downloaded_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()


# ── Ratings ────────────────────────────────────────────────────────────────────

def rate_resource(user_id: int, book_id: int, rating: int) -> None:
    """Insert or update a user's rating for a resource (1-5)."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO ratings (user_id, book_id, rating, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                rating     = excluded.rating,
                updated_at = excluded.updated_at
        """, (user_id, book_id, rating))
        conn.commit()


def get_rating_stats(book_id: int) -> dict:
    """Return avg rating (1 decimal) and count for a resource."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT ROUND(AVG(rating), 1) AS avg, COUNT(*) AS cnt "
            "FROM ratings WHERE book_id = ?",
            (book_id,)
        ).fetchone()
    avg = row["avg"] if row and row["avg"] is not None else None
    cnt = row["cnt"] if row else 0
    return {"avg": avg, "cnt": cnt}


def get_user_rating(user_id: int, book_id: int) -> Optional[int]:
    """Return the user's existing rating for a resource, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT rating FROM ratings WHERE user_id = ? AND book_id = ?",
            (user_id, book_id)
        ).fetchone()
    return row["rating"] if row else None


def get_books_by_field(physics_field: str, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE physics_field = ? ORDER BY title LIMIT ?",
            (physics_field, limit)
        ).fetchall()


def get_field_counts(physics_field: str) -> dict:
    """Return the number of books and articles for a given physics field."""
    with get_connection() as conn:
        book_count = conn.execute(
            "SELECT COUNT(*) FROM books WHERE physics_field = ? AND resource_type = 'book'",
            (physics_field,)
        ).fetchone()[0]
        article_count = conn.execute(
            "SELECT COUNT(*) FROM books WHERE physics_field = ? AND resource_type = 'article'",
            (physics_field,)
        ).fetchone()[0]
    return {"books": book_count, "articles": article_count}


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

# Most Downloaded Resources
def get_top_downloads(limit: int = 10, resource_type: str = "", offset: int = 0) -> list[sqlite3.Row]:
    """Rank by a Bayesian score that blends download_count with average rating.

    score = download_count + (rating_weight * bayesian_avg)
    bayesian_avg = (n*avg + C*m) / (n + C)
      n = number of ratings for this resource
      avg = resource's mean rating
      m = global mean rating across all rated resources (fallback 3.0)
      C = confidence constant (min ratings before rating pulls the score)
    rating_weight scales rating contribution relative to downloads.
    """
    where = "WHERE resource_type = ?" if resource_type else ""
    params_list: list = [resource_type] if resource_type else []

    # Global mean and confidence constant
    C = 5          # need at least 5 ratings before they heavily influence rank
    W = 20         # one full "quality point" == W extra imaginary downloads
    # (tune: a 5-star resource with >=C ratings earns up to ~2*W extra virtual downloads)

    sql = f"""
        SELECT b.*,
               COALESCE(r.avg_r, 0)   AS avg_rating,
               COALESCE(r.cnt_r, 0)   AS rating_count,
               (
                   b.download_count
                   + {W} * (
                       (COALESCE(r.cnt_r,0) * COALESCE(r.avg_r,0) + {C} * COALESCE(gm.global_mean, 3.0))
                       / (COALESCE(r.cnt_r,0) + {C})
                       - COALESCE(gm.global_mean, 3.0)
                   )
               ) AS score
        FROM books b
        LEFT JOIN (
            SELECT book_id, AVG(rating) AS avg_r, COUNT(*) AS cnt_r
            FROM ratings GROUP BY book_id
        ) r ON r.book_id = b.id
        LEFT JOIN (
            SELECT AVG(rating) AS global_mean FROM ratings
        ) gm ON 1=1
        {where}
        ORDER BY score DESC
        LIMIT ? OFFSET ?
    """
    params_list += [limit, offset]
    with get_connection() as conn:
        return conn.execute(sql, params_list).fetchall()


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
        total_users    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {
        "total_books":      total_books,
        "total_articles":   total_articles,
        "total_resources":  total_resources,
        "fa_books":         total_fa,
        "en_books":         total_en,
        "total_downloads":  total_dl,
        "unique_fields":    unique_fields,
        "total_users":      total_users,
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

# Admin Lists
def list_admins() -> list[sqlite3.Row]:
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

# ── Pending Resources (Phase 1: Foundation) ───────────────────────────────────
#
# These functions manage the pending_resources table.  A pending resource
# becomes a real library entry only when published via add_resource().
# field_number is never assigned here — that happens at publish time.

_PENDING_ALLOWED_FIELDS = {
    "resource_type", "title", "author", "language", "physics_field",
    "description", "edition", "year",
    "doi", "journal", "volume", "issue", "pages", "url", "publication_date",
    "file_id", "file_name", "file_size",
    "status",
}


def _validate_pending_fields(
    resource_type: Optional[str] = None,
    language: Optional[str] = None,
    physics_field: Optional[str] = None,
) -> None:
    """Raise ValueError for invalid resource_type / language / physics_field.

    Uses the same rules as add_resource() in the existing library system.
    """
    if resource_type is not None and resource_type not in ("book", "article"):
        raise ValueError("resource_type باید 'book' یا 'article' باشد")
    if language is not None and language not in ("fa", "en"):
        raise ValueError("زبان باید 'fa' یا 'en' باشد")
    if physics_field is not None and physics_field not in PHYSICS_FIELDS:
        raise ValueError(f"فیلد نامعتبر: {physics_field}")


def create_pending_resource(
    title: str,
    author: str,
    language: str,
    physics_field: str,
    resource_type: str = "book",
    description: str = "",
    edition: str = "",
    year: Optional[int] = None,
    doi: str = "",
    journal: str = "",
    volume: str = "",
    issue: str = "",
    pages: str = "",
    url: str = "",
    publication_date: str = "",
) -> int:
    """Insert a new pending resource (metadata only; no file yet).

    Returns the new pending resource id.
    Raises ValueError for invalid resource_type, language, or physics_field.
    """
    _validate_pending_fields(resource_type, language, physics_field)

    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO pending_resources
                (resource_type, title, author, language, physics_field,
                 description, edition, year,
                 doi, journal, volume, issue, pages, url, publication_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            resource_type, title, author, language, physics_field,
            description, edition, year,
            doi, journal, volume, issue, pages, url, publication_date,
        ))
        conn.commit()
        return cur.lastrowid


def bulk_create_pending_resources(rows: list[dict]) -> list[int]:
    """Insert multiple pending resources atomically inside one transaction.

    *rows* is a list of kwargs dicts, each valid for create_pending_resource().
    All validation is performed before any INSERT; if any row is invalid the
    entire batch is rejected with ValueError and nothing is written.
    On a database error mid-insert the transaction is rolled back automatically
    (sqlite3 context manager) and the exception is re-raised.

    Returns the list of new pending resource ids in the same order as *rows*.
    Existing create_pending_resource() behaviour is unchanged.
    """
    if not rows:
        return []

    # Validate every row first — no DB work yet.
    for i, row in enumerate(rows):
        _validate_pending_fields(
            resource_type=row.get("resource_type"),
            language=row.get("language"),
            physics_field=row.get("physics_field"),
        )
        if not row.get("title"):
            raise ValueError(f"row {i}: title is required")
        if not row.get("author"):
            raise ValueError(f"row {i}: author is required")

    # Single connection — sqlite3's context manager issues COMMIT on __exit__
    # and ROLLBACK on exception, giving us true atomicity.
    ids: list[int] = []
    with get_connection() as conn:
        for row in rows:
            cur = conn.execute("""
                INSERT INTO pending_resources
                    (resource_type, title, author, language, physics_field,
                     description, edition, year,
                     doi, journal, volume, issue, pages, url, publication_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row.get("resource_type", "book"),
                row["title"],
                row["author"],
                row["language"],
                row["physics_field"],
                row.get("description", ""),
                row.get("edition", ""),
                row.get("year"),
                row.get("doi", ""),
                row.get("journal", ""),
                row.get("volume", ""),
                row.get("issue", ""),
                row.get("pages", ""),
                row.get("url", ""),
                row.get("publication_date", ""),
            ))
            ids.append(cur.lastrowid)
        conn.commit()
    return ids


def get_pending_resource(pending_id: int) -> Optional[sqlite3.Row]:
    """Return a single pending resource row by its internal id, or None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pending_resources WHERE id = ?", (pending_id,)
        ).fetchone()


def update_pending_resource(pending_id: int, **kwargs) -> bool:
    """Update allowed metadata fields on a pending resource.

    Returns True if a row was updated, False if the id was not found or no
    valid fields were supplied.  Raises ValueError for constraint violations.
    """
    fields = {k: v for k, v in kwargs.items() if k in _PENDING_ALLOWED_FIELDS}
    if not fields:
        return False

    # Validate constrained fields if they are being changed
    _validate_pending_fields(
        resource_type=fields.get("resource_type"),
        language=fields.get("language"),
        physics_field=fields.get("physics_field"),
    )

    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [pending_id]

    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE pending_resources SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0


def delete_pending_resource(pending_id: int) -> bool:
    """Delete a pending resource row.  Returns True if a row was deleted."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM pending_resources WHERE id = ?", (pending_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def list_pending_resources(
    status: str = "",
    resource_type: str = "",
    physics_field: str = "",
    language: str = "",
    query: str = "",
    limit: int = 20,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Return pending resources filtered by optional criteria.

    Filters are ANDed together.  *query* does a case-insensitive substring
    match across title and author (same lightweight approach as search_books).
    Results are ordered newest-first (created_at DESC).
    """
    conditions: list[str] = []
    params: list = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if resource_type:
        conditions.append("resource_type = ?")
        params.append(resource_type)
    if physics_field:
        conditions.append("physics_field = ?")
        params.append(physics_field)
    if language:
        conditions.append("language = ?")
        params.append(language)
    if query:
        like = f"%{query}%"
        conditions.append("(title LIKE ? OR author LIKE ?)")
        params += [like, like]

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT * FROM pending_resources
        {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    with get_connection() as conn:
        return conn.execute(sql, params).fetchall()


def attach_pending_file(
    pending_id: int,
    file_id: str,
    file_name: str,
    file_size: int,
) -> bool:
    """Record the Telegram file details on a pending resource and advance its
    status to 'file_received' (unless it was already published/rejected).

    Returns True if the row was updated.
    """
    with get_connection() as conn:
        # Only advance status when the resource is still pending
        cur = conn.execute("""
            UPDATE pending_resources
            SET file_id    = ?,
                file_name  = ?,
                file_size  = ?,
                status     = CASE
                                 WHEN status = 'pending' THEN 'file_received'
                                 ELSE status
                             END,
                updated_at = ?
            WHERE id = ?
        """, (file_id, file_name, file_size, datetime.utcnow().isoformat(), pending_id))
        conn.commit()
        return cur.rowcount > 0


def update_pending_status(pending_id: int, status: str) -> bool:
    """Set the status of a pending resource explicitly.

    Valid values: 'pending', 'file_received', 'published', 'rejected'.
    Returns True if the row was updated, raises ValueError for unknown status.
    """
    valid = {"pending", "file_received", "published", "rejected"}
    if status not in valid:
        raise ValueError(f"وضعیت نامعتبر: {status!r}. مقادیر مجاز: {valid}")

    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE pending_resources SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(), pending_id),
        )
        conn.commit()
        return cur.rowcount > 0


def find_library_duplicates(
    resource_type: str,
    title: str,
    author: str,
    doi: str = "",
) -> list:
    """Lightweight duplicate detection against the existing Library (books table).

    Matches on:
      - resource_type AND normalised title (case-insensitive) AND normalised author
      - OR (for articles) resource_type AND non-empty DOI match

    Returns a list of matching sqlite3.Row objects (may be empty).
    """
    results = []
    seen_ids: set[int] = set()

    title_norm  = title.strip().lower()
    author_norm = author.strip().lower()

    with get_connection() as conn:
        # Primary match: type + title + author (both normalised)
        rows = conn.execute(
            "SELECT * FROM books "
            "WHERE resource_type = ? "
            "  AND LOWER(TRIM(title))  = ? "
            "  AND LOWER(TRIM(author)) = ?",
            (resource_type, title_norm, author_norm),
        ).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                results.append(r)

        # Secondary match for articles: non-empty DOI
        if resource_type == "article" and doi and doi.strip():
            doi_norm = doi.strip().lower()
            rows2 = conn.execute(
                "SELECT * FROM books "
                "WHERE resource_type = 'article' "
                "  AND LOWER(TRIM(doi)) = ?",
                (doi_norm,),
            ).fetchall()
            for r in rows2:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    results.append(r)

    return results


def publish_pending_resource(pending_id: int, added_by: Optional[int] = None) -> int:
    """Atomically publish a pending resource into the Library.

    Steps (all inside one SQLite connection, within an explicit transaction):
      1. Re-fetch and validate the pending row (must exist, status == 'file_received',
         file_id must be non-empty).
      2. Call add_resource() logic *within the same connection* to insert the books row
         and obtain its new id.
      3. Only if insertion succeeds, mark the pending row as 'published'.
      4. Commit once — both writes land together or neither does.

    Returns the new library resource id (books.id) on success.
    Raises ValueError for validation failures.
    Raises RuntimeError for unexpected DB errors.

    NOTE: add_resource() opens its own connection internally, which would break
    atomicity.  To avoid duplicating its logic we inline the insertion here using
    the same connection, replicating only the field_number + INSERT that
    add_resource() performs.  This is the minimal safe approach that preserves
    all existing logic (field_number generation, display ID generation, etc.)
    without modifying add_resource().
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")

        # ── 1. Validate pending row ────────────────────────────────────────────
        row = conn.execute(
            "SELECT * FROM pending_resources WHERE id = ?", (pending_id,)
        ).fetchone()

        if row is None:
            raise ValueError(f"pending resource P{pending_id} وجود ندارد")

        if row["status"] == "published":
            raise ValueError(f"P{pending_id} قبلاً منتشر شده — انتشار مجدد مجاز نیست")

        if row["status"] != "file_received":
            raise ValueError(
                f"P{pending_id} وضعیت '{row['status']}' دارد؛ "
                f"انتشار فقط برای 'file_received' مجاز است"
            )

        if not row["file_id"]:
            raise ValueError(f"P{pending_id} فاقد اطلاعات فایل است")

        # ── 2. Validate fields for add_resource ───────────────────────────────
        physics_field = row["physics_field"]
        if physics_field not in PHYSICS_FIELDS:
            raise ValueError(f"فیلد فیزیکی نامعتبر: {physics_field}")

        language      = row["language"]
        if language not in ("fa", "en"):
            raise ValueError(f"زبان نامعتبر: {language}")

        resource_type = row["resource_type"]
        if resource_type not in ("book", "article"):
            raise ValueError(f"نوع منبع نامعتبر: {resource_type}")

        # ── 3. Compute next field_number (same logic as add_resource) ──────────
        fn_row = conn.execute(
            "SELECT COALESCE(MAX(field_number), 0) AS mx FROM books "
            "WHERE physics_field = ? AND resource_type = ?",
            (physics_field, resource_type),
        ).fetchone()
        next_number = fn_row["mx"] + 1

        # ── 4. Insert into books (mirrors add_resource INSERT exactly) ─────────
        cur = conn.execute("""
            INSERT INTO books
                (title, author, language, physics_field,
                 description, edition, year,
                 file_id, file_name, file_size,
                 cover_file_id, added_by, field_number, resource_type,
                 doi, journal, volume, issue, pages, url, publication_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row["title"],
            row["author"],
            language,
            physics_field,
            row["description"] or "",
            row["edition"] or "",
            row["year"],
            row["file_id"],
            row["file_name"] or "",
            row["file_size"] or 0,
            "",            # cover_file_id — not stored in pending
            added_by,
            next_number,
            resource_type,
            row["doi"] or "",
            row["journal"] or "",
            row["volume"] or "",
            row["issue"] or "",
            row["pages"] or "",
            row["url"] or "",
            row["publication_date"] or "",
        ))
        new_id = cur.lastrowid

        # ── 5. Mark pending row as published (only after successful INSERT) ─────
        conn.execute(
            "UPDATE pending_resources SET status = 'published', updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), pending_id),
        )

        conn.commit()
        return new_id

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


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