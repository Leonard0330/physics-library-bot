import sqlite3
import os
import re
import unicodedata
from functools import lru_cache
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
    _migrate_db()


def _migrate_db() -> None:
    """Apply incremental migrations to an existing database.

    Safe to run on every startup: every statement uses CREATE TABLE IF NOT EXISTS
    or ALTER TABLE only when the column/table is absent.  Never drops or modifies
    existing data.

    Migrations are listed in chronological order so they can be extended easily.
    """
    with get_connection() as conn:
        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        # ── Migration 1: pending_resources (added in Phase 1) ─────────────────
        if "pending_resources" not in existing_tables:
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
            print("[DB] Migration applied: pending_resources table created.")

        # ── Future migrations go here ──────────────────────────────────────────
        # Example pattern:
        #   pending_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending_resources)")}
        #   if "new_column" not in pending_cols:
        #       conn.execute("ALTER TABLE pending_resources ADD COLUMN new_column TEXT NOT NULL DEFAULT ''")
        #       conn.commit()


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


# ── Search: normalization & relevance ranking ─────────────────────────────────
#
# Text (both the query and the stored fields) is normalised the same way:
# NFKD → drop combining marks (Latin accents, Arabic harakat, hamza/madda
# marks) → ي/ى→ی, ك→ک, ة/ۀ→ه → Arabic/Persian digits → ASCII → casefold →
# every run of punctuation / hyphens / underscores / ZWNJ becomes one space.

_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "ہ": "ه", "ھ": "ه",
    "\u200c": " ",                                    # ZWNJ → word separator
    "\u0640": None, "\u200d": None, "\u200e": None,   # tatweel, ZWJ, LRM
    "\u200f": None, "\ufeff": None,                   # RLM, BOM
})
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NON_WORD_RE = re.compile(r"[\W_]+")
_DOI_PREFIX_RE = re.compile(r"^\s*(?:https?://)?(?:dx\.)?doi\.org/|^\s*doi:\s*", re.I)

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")

# Retrieval-only aliases: they expand the *query* and are never stored or shown.
# Keys are normalised (lower-case, single spaces).
_SEARCH_ALIASES = {
    "qm":             "quantum mechanics",
    "qft":            "quantum field theory",
    "em":             "electromagnetism",
    "gr":             "general relativity",
    "sr":             "special relativity",
    "stat mech":      "statistical mechanics",
    "classical mech": "classical mechanics",
    "thermo":         "thermodynamics",
}
_ALIAS_RE = re.compile(
    r"(?<!\S)(" + "|".join(re.escape(k) for k in sorted(_SEARCH_ALIASES, key=len, reverse=True)) + r")(?!\S)"
)

# Ignored as search tokens (unless the query consists of nothing else).
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "by", "with",
    "و", "در", "از", "به", "با", "برای", "را", "این", "آن", "که", "تا",
})

# Field-class weights: title, author, other metadata, description.
_FIELD_W = (60, 40, 25, 10)
# Match-kind factors: 4 whole word, 3 word prefix, 2 substring, 1 ignoring spaces.
_KIND_F = {4: 1.0, 3: 0.8, 2: 0.5, 1: 0.4}
_FUZZY_F = 0.3      # typo matches weigh less; they are also always listed after exact ones


@lru_cache(maxsize=50_000)
def _norm(text) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.translate(_CHAR_MAP).translate(_DIGIT_MAP).casefold()
    return _NON_WORD_RE.sub(" ", s).strip()


def _prep(text) -> tuple:
    t = _norm(text)
    w = t.split()
    return (t, w, frozenset(w), t.replace(" ", ""))


def _prepare_query(query: str) -> Optional[dict]:
    raw = (query or "").strip()
    hash_id = raw.startswith("#")
    stripped = _DOI_PREFIX_RE.sub("", raw).strip()
    q_norm = _norm(stripped)
    tokens = _tokenize(q_norm)
    if not tokens:
        return None
    q = {"norm": q_norm, "sq": q_norm.replace(" ", ""),
         "tokens": tokens, "hash_id": hash_id, "doi": None, "alt": None}

    doi = stripped.rstrip(".,;:)]}>\"'").lower()
    if _DOI_RE.fullmatch(doi):
        q["doi"] = doi                       # DOI query: only DOI matches count
        return q

    # Alias variant (e.g. "qm" -> "quantum mechanics"); scored alongside the original.
    alt_norm = q_norm if hash_id else _ALIAS_RE.sub(lambda m: _SEARCH_ALIASES[m.group(1)], q_norm)
    if alt_norm != q_norm:
        q["alt"] = {"norm": alt_norm, "sq": alt_norm.replace(" ", ""),
                    "tokens": _tokenize(alt_norm), "hash_id": False,
                    "doi": None, "alt": None}
    return q


def _tokenize(norm: str) -> list:
    tokens = list(dict.fromkeys(norm.split()))
    return [t for t in tokens if t not in _STOPWORDS] or tokens


@lru_cache(maxsize=20_000)
def _typo_dist(a: str, b: str) -> int:
    """Damerau-Levenshtein (adjacent transposition counts as one edit)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 1)
            cur.append(v)
        prev2, prev = prev, cur
    return prev[-1]


def _fuzzy_hit(tok: str, words) -> bool:
    """Controlled typo match: tokens of 6+ letters only (so qm/em/gr/qft never
    fuzz), same first letter, 1 edit (2 edits for 10+ letters)."""
    if len(tok) < 6 or not tok.isalpha():
        return False
    d = 1 if len(tok) <= 9 else 2
    return any(
        len(w) >= 4 and abs(len(w) - len(tok)) <= d and w[0] == tok[0]
        and _typo_dist(tok, w) <= d
        for w in words
    )


def _author_kind(tok: str, author: tuple, n_tokens: int) -> int:
    """Name variations: "d griffiths" (initial), "jj sakurai" (joined initials)."""
    if not author[0]:
        return 0
    if len(tok) == 1 and n_tokens > 1:
        return 3 if any(w.startswith(tok) for w in author[1]) else 0
    initials = "".join(w for w in author[1] if len(w) == 1)
    return 3 if 2 <= len(tok) <= 3 and tok in initials else 0


def _kind(tok: str, f: tuple) -> int:
    text, words, wset, sq = f
    if tok in wset:
        return 4
    n = len(tok)
    if n < 2 or not text:
        return 0
    if any(w.startswith(tok) for w in words):
        return 3
    if n >= 3 and tok in text:
        return 2
    if n >= 4 and tok in sq:
        return 1
    return 0


def _default_cover(n: int) -> int:
    return n if n < 3 else n - 1                # 3+ tokens: tolerate one missing


def _score_row(row, q: dict, min_cover: Optional[int]) -> Optional[tuple]:
    """Best (tier, score, fuzzy) over the original query and its alias variant."""
    best = None
    for v in (q, q.get("alt")):
        if not v:
            continue
        if min_cover is not None:
            mc = min_cover
        elif v is q:
            mc = _default_cover(len(q["tokens"]))
        else:   # alias expansion adds tokens; only allow the slack the original query had
            mc = max(1, len(v["tokens"]) - (len(q["tokens"]) - _default_cover(len(q["tokens"]))))
        res = _score_variant(row, v, mc)
        if res and (best is None or (res[2], -res[0], -res[1]) < (best[2], -best[0], -best[1])):
            best = res
    return best


def _score_variant(row, q: dict, min_cover: int) -> Optional[tuple]:
    """Return (tier, score, used_fuzzy) or None when the row does not match.

    tier 8 exact DOI (DOI queries only match DOIs)

    tier 7 exact title / exact identifier (display ID, DOI)
         6 title phrase      5 all tokens in title     4 … title + author
         3 … + other metadata (journal, DOI, edition, year, field, ID)
         2 … + description   1 weak partial (some tokens missing)
    """
    keys = row.keys()

    def g(k):
        v = row[k] if k in keys else None
        return "" if v is None else str(v)

    if q["doi"]:
        row_doi = _DOI_PREFIX_RE.sub("", g("doi")).strip().lower()
        if row_doi == q["doi"]:
            return (8, 10_000, False)
        return (5, 5_000, False) if row_doi and row_doi.startswith(q["doi"]) else None

    title, author, desc = _prep(g("title")), _prep(g("author")), _prep(g("description"))
    qn, qsq, tokens = q["norm"], q["sq"], q["tokens"]

    # ── tier 7: exact title or exact identifier
    try:
        disp = get_display_id(row)
    except Exception:
        disp = ""
    idents = set()
    disp_sq = _norm(disp).replace(" ", "")
    if disp_sq and (q["hash_id"] or not disp.lstrip("#").isdigit()):
        idents.add(disp_sq)
        # Also add the no-type-prefix variant so "#QM-7" matches books displayed as "#QM-B7"
        no_type = re.sub(r'^([a-z]+)[ab](\d+)$', r'\1\2', disp_sq)
        if no_type != disp_sq:
            idents.add(no_type)
    if g("field_number") and g("resource_type") != "article":      # legacy "QM-7"
        idents.add(_norm(field_code(g("physics_field"))).replace(" ", "") + g("field_number"))
    doi_sq = _norm(g("doi")).replace(" ", "")
    if doi_sq:
        idents.add(doi_sq)
    if qsq in idents or qn == title[0] or (qsq and qsq == title[3]):
        return (7, 10_000, False)

    fa, en = PHYSICS_FIELDS.get(g("physics_field"), ("", ""))
    meta = [p for p in (_prep(x) for x in (
        g("journal"), g("doi"), g("edition"), g("year"), g("publication_date"),
        g("file_name"), fa, en, g("physics_field"), disp)) if p[0]]
    groups = ([title], [author], meta, [desc])

    covered, worst, total, title_hit, ta_hit, fuzzy = 0, 0, 0.0, False, False, False
    for tok in tokens:
        best_w, best_c, tok_fuzzy = 0.0, None, False
        for c, group in enumerate(groups):
            k = max((_kind(tok, f) for f in group), default=0)
            if not k and c == 1:
                k = _author_kind(tok, author, len(tokens))
            if k and _FIELD_W[c] * _KIND_F[k] > best_w:
                best_w, best_c = _FIELD_W[c] * _KIND_F[k], c
        if best_c is None and tok not in q.get("known", ()):
            # typo tolerance (title/author/metadata only) — skipped for words that
            # are real words in the library, so "mechanics" never fuzzes to "mechanism"
            for c in (0, 1, 2):
                if any(_fuzzy_hit(tok, f[1]) for f in groups[c]):
                    best_w, best_c, tok_fuzzy = _FIELD_W[c] * _FUZZY_F, c, True
                    break
        if best_c is not None:
            fuzzy = fuzzy or tok_fuzzy
            covered += 1
            worst = max(worst, best_c)
            total += best_w
            title_hit = title_hit or best_c == 0
            ta_hit = ta_hit or best_c <= 1
    if covered < min_cover:
        return None
    if covered < len(tokens) and not ta_hit:
        return None          # partial matches need a title/author hit (not just field/ID)

    tier = (5, 4, 3, 2)[worst] if covered == len(tokens) else 1

    # ── tier 6: query is a phrase in the title (word-start; whole word for 1-char queries)
    t_pad = f" {title[0]} "
    if (f" {qn}" if len(qsq) >= 2 else f" {qn} ") in t_pad:
        tier = 6
        total += 40 if title[0].startswith(qn) else 0
        total += 10 if f" {qn} " in t_pad else 0
    if len(qsq) >= 2 and f" {qn}" in f" {author[0]}":
        total += 30
    if title_hit:                       # prefer titles that are mostly the query
        total += 20 * min(1.0, len(tokens) / max(1, len(title[1])))
    return (tier, int(total), fuzzy)


def _rank_rows(rows, q: dict, order_by: str = "default", min_cover: Optional[int] = None) -> list:
    if any(len(t) >= 6 and t.isalpha() for v in (q, q.get("alt")) if v for t in v["tokens"]):
        known = set()
        for row in rows:
            known.update(_prep(row["title"])[1])
            known.update(_prep(row["author"])[1])
        q["known"] = known
        if q.get("alt"):
            q["alt"]["known"] = known
    scored = []
    for idx, row in enumerate(rows):
        res = _score_row(row, q, min_cover)
        if res:
            scored.append((res, idx, row))
    # Fuzzy (typo) matches always come after exact/normal ones.
    # For all order_by values: sort by (fuzzy, -tier, -score, original_sql_idx).
    # The original SQL ordering (recent/popular) is preserved as a tiebreak via idx,
    # so exact matches still rank above weak partial matches even in recent/popular lists.
    scored.sort(key=lambda s: (s[0][2], -s[0][0], -s[0][1], s[1]))
    return [s[2] for s in scored]


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

    # Text search: the filters below are applied in SQL, then rows are matched
    # and ranked in Python (see _score_row) so normalisation is consistent for
    # query and stored text.  Without a query the original SQL path is used.
    q_info: Optional[dict] = None
    if query:
        q_info = _prepare_query(query)
        if q_info is None:
            return []        # nothing searchable (only punctuation/whitespace)

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
    elif order_by == "popular":
        order_clause = "ORDER BY download_count DESC, created_at DESC"
    else:
        # default: alphabetical — also the tie-break for relevance ranking
        order_clause = "ORDER BY title ASC, id ASC"

    if q_info is not None:
        with get_connection() as conn:
            rows = conn.execute(f"SELECT * FROM books {where} {order_clause}", params).fetchall()
            # Build the "known" word set from the full library so that narrow filters
            # don't prevent real words from suppressing spurious fuzzy matches.
            if any(len(t) >= 6 and t.isalpha()
                   for v in (q_info, q_info.get("alt")) if v
                   for t in v["tokens"]):
                known: set[str] = set()
                for r in conn.execute("SELECT title, author FROM books").fetchall():
                    known.update(_prep(r["title"])[1])
                    known.update(_prep(r["author"])[1])
                q_info["known"] = known
                if q_info.get("alt"):
                    q_info["alt"]["known"] = known
        return _rank_rows(rows, q_info, order_by)[offset:offset + limit]

    sql = f"""
        SELECT * FROM books
        {where}
        {order_clause}
        LIMIT ? OFFSET ?
    """

    with get_connection() as conn:
        return conn.execute(sql, params + [limit, offset]).fetchall()


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
    q_info = _prepare_query(query)
    if not q_info:
        return []
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY title ASC, id ASC").fetchall()
    # Relaxed matching: a single matching token is enough, best matches first.
    return _rank_rows(rows, q_info, min_cover=1)[:limit]


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
        has_pending_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_resources'"
        ).fetchone()
        if has_pending_table:
            pending_total = conn.execute(
                "SELECT COUNT(*) FROM pending_resources WHERE status NOT IN ('published', 'rejected')"
            ).fetchone()[0]
            pending_awaiting_file = conn.execute(
                "SELECT COUNT(*) FROM pending_resources WHERE status = 'pending'"
            ).fetchone()[0]
            pending_file_received = conn.execute(
                "SELECT COUNT(*) FROM pending_resources WHERE status = 'file_received'"
            ).fetchone()[0]
        else:
            pending_total = pending_awaiting_file = pending_file_received = 0
    return {
        "total_books":            total_books,
        "total_articles":         total_articles,
        "total_resources":        total_resources,
        "fa_books":               total_fa,
        "en_books":               total_en,
        "total_downloads":        total_dl,
        "unique_fields":          unique_fields,
        "total_users":            total_users,
        "pending_total":          pending_total,
        "pending_awaiting_file":  pending_awaiting_file,
        "pending_file_received":  pending_file_received,
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
    """Search the main library (books table) for possible duplicates.

    A duplicate is detected when:
      - DOI matches exactly (if doi is non-empty), OR
      - title AND author are similar (case-insensitive substring match on
        first 40 chars of each field — enough to catch minor spelling diffs).

    Returns a list of matching sqlite3.Row objects (usually 0 or 1 item).
    """
    conditions: list[str] = []
    params: list = []

    if doi:
        conditions.append("(doi = ? AND doi != '')")
        params.append(doi)

    title_prefix  = title[:40].lower()
    author_prefix = author[:40].lower()
    conditions.append(
        "(LOWER(SUBSTR(title, 1, 40)) = ? AND LOWER(SUBSTR(author, 1, 40)) = ?)"
    )
    params += [title_prefix, author_prefix]

    where = "WHERE resource_type = ? AND (" + " OR ".join(conditions) + ")"
    all_params = [resource_type] + params

    with get_connection() as conn:
        return conn.execute(
            f"SELECT * FROM books {where} LIMIT 5",
            all_params,
        ).fetchall()


def publish_pending_resource(pending_id: int, added_by: Optional[int] = None) -> int:
    """Atomically publish a pending resource into the main library.

    Steps:
      1. Fetch and validate the pending row (must be status='file_received'
         and have a non-empty file_id).
      2. Insert into the books table via add_resource() so that field_number
         is assigned correctly.
      3. Mark the pending row as 'published'.

    Returns the new library resource id (books.id).
    Raises ValueError if the resource is not ready to publish or not found.
    Raises RuntimeError on unexpected database errors.
    """
    row = get_pending_resource(pending_id)
    if not row:
        raise ValueError(f"منبع در انتظار با شناسه {pending_id} پیدا نشد.")

    if row["status"] == "published":
        raise ValueError("این منبع قبلاً منتشر شده است.")

    if row["status"] != "file_received":
        raise ValueError(
            f"منبع هنوز آماده انتشار نیست (وضعیت: {row['status']})."
        )

    if not row["file_id"]:
        raise ValueError("فایل منبع هنوز آپلود نشده است.")

    # Insert into the main library using the existing add_resource() path
    # so that field_number is assigned atomically.
    new_id = add_resource(
        title            = row["title"],
        author           = row["author"],
        language         = row["language"],
        physics_field    = row["physics_field"],
        resource_type    = row["resource_type"],
        file_id          = row["file_id"],
        file_name        = row["file_name"] or "",
        file_size        = row["file_size"] or 0,
        description      = row["description"] or "",
        edition          = row["edition"] or "",
        year             = row["year"],
        added_by         = added_by,
        doi              = row["doi"] or "",
        journal          = row["journal"] or "",
        volume           = row["volume"] or "",
        issue            = row["issue"] or "",
        pages            = row["pages"] or "",
        url              = row["url"] or "",
        publication_date = row["publication_date"] or "",
    )

    # Mark the pending row as published so it cannot be published twice.
    update_pending_status(pending_id, "published")

    return new_id


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