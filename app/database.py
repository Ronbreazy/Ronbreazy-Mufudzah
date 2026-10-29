"""
database.py
-----------
Everything related to storing and reading data, using Python's built-in
`sqlite3` module. SQLite keeps the whole database in one file
(zimlegal.db) -- there is nothing extra to install or run.

Every function in this file opens its own short-lived connection, does its
work, and closes it again. That keeps things simple and thread-safe enough
for a small app like this (the http.server we use later can handle a
request per thread).
"""

import sqlite3
import re
import json
import datetime
from app.config import DATABASE_PATH


def get_connection():
    """Open a new connection to the database file.

    `row_factory = sqlite3.Row` lets us access columns by name
    (row["email"]) instead of only by position (row[2]), which makes the
    rest of the code much easier to read.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't already exist, and add starter data
    the first time the app runs (an empty forum/directory would make for a
    very empty-looking app on first launch).
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS forum_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_name TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS case_law (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            simplified_text TEXT NOT NULL,
            citations TEXT NOT NULL,   -- stored as a JSON array string
            court_tier TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS directory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,          -- legal_aid_ngo | state | private_lawyer | law_firm
            focus_areas TEXT NOT NULL,   -- stored as a JSON array string
            location TEXT NOT NULL,
            contact TEXT NOT NULL,
            description TEXT NOT NULL,
            fee_basis TEXT NOT NULL,     -- 'free' or 'paid'
            search_only INTEGER NOT NULL DEFAULT 0  -- 1 = hidden from the browse list, found only by searching
        )
    """)

    # A directory table created by an earlier version has no search_only column.
    directory_columns = [row["name"] for row in cur.execute("PRAGMA table_info(directory)").fetchall()]
    if "search_only" not in directory_columns:
        cur.execute("ALTER TABLE directory ADD COLUMN search_only INTEGER NOT NULL DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,       -- shown to the user, e.g. "Domestic Violence Act"
            file_name TEXT NOT NULL,    -- which file in knowledge_base/ it came from
            file_key TEXT NOT NULL DEFAULT '',  -- fingerprint of that file when it was read (see knowledge.py)
            created_at TEXT NOT NULL
        )
    """)

    # A knowledge table created by an earlier version has no file_key column.
    # Add it; the old rows get a blank key, which never matches a real
    # fingerprint, so those files are simply read again once.
    knowledge_columns = [row["name"] for row in cur.execute("PRAGMA table_info(knowledge)").fetchall()]
    if "file_key" not in knowledge_columns:
        cur.execute("ALTER TABLE knowledge ADD COLUMN file_key TEXT NOT NULL DEFAULT ''")

    # Legal Dictionary answers from the AI, saved so each word is only ever
    # asked once (see app/dictionary.py). Safe to empty at any time -- words
    # are simply looked up again.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dictionary_cache (
            term TEXT PRIMARY KEY,       -- the tidied-up word, e.g. "ex parte"
            data TEXT NOT NULL,          -- the whole answer, as a JSON object string
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()

    # Add any directory entries that aren't in the database yet (matched by
    # name). This runs on every start, so a database created by an earlier
    # version picks up newly added firms without being deleted, and nothing
    # is ever duplicated.
    _sync_directory(cur)

    # Case law is intentionally NOT seeded with sample data. The frontend's
    # "Case Law" library is meant to show real entries built from what
    # people actually run through the Judgment Summarizer (see
    # create_case_law_entry() below, called from the /simplify handler) --
    # seeding it with fake placeholder judgments would misrepresent them as
    # real case law, and would clutter the library with entries that have
    # nothing to do with real submissions.

    cur.execute("SELECT COUNT(*) AS n FROM forum_posts")
    if cur.fetchone()["n"] == 0:
        _seed_forum(cur)

    conn.commit()
    conn.close()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# Directory entries, as (name, type, focus_areas, location, contact, description, fee_basis, search_only).
#
# search_only=False -> shown in the normal browse list.
# search_only=True  -> NOT shown in the browse list; it appears only when
#                      someone searches (GET /directory?q=...). That keeps the
#                      page tidy while still letting people find any firm.
#
# The first seven are the starter entries; the rest come from a pasted list of
# Harare firms (Google listings). Their ratings are a snapshot and they have
# no street address or confirmed pro-bono arrangement, so fee_basis is "paid"
# (no Pro Bono badge) and the location is just "Harare". Verify before relying
# on a number -- the directory page itself says inclusion is not a referral.
_DIRECTORY_ENTRIES = [
    ("Legal Aid Directorate", "state", ["Civil", "Family", "Criminal"],
     "Harare Central, Rotten Row", "+263 242 794 200",
     "Government-funded legal assistance (Ministry of Justice) for those who qualify.", "free", False),
    ("Zimbabwe Women Lawyers Association (ZWLA)", "legal_aid_ngo", ["Family", "Inheritance", "Protection Orders"],
     "94 Park Lane, Harare", "+263 242 790 412",
     "Free legal aid for women, focusing on family, inheritance and protection.", "free", False),
    ("Advocacy and Legal Advice Centre (ALAC)", "legal_aid_ngo", ["Civil", "Human Rights"],
     "21 Baker Avenue, Harare", "+263 242 700 542",
     "Free legal advice and representation for vulnerable groups.", "free", False),
    # Contact details from each organisation's own published notices (ZLHR's
    # dates from 2020) -- confirm by phone before go-live.
    ("Zimbabwe Lawyers for Human Rights (ZLHR)", "legal_aid_ngo", ["Human Rights", "Criminal", "Constitutional"],
     "Kodzero House, 103 Sam Nujoma Street, Harare", "+263 242 764085",
     "Free legal assistance in human rights matters, including mobile legal clinics. "
     "Emergency hotline (Harare): +263 772 257 247.", "free", False),
    ("Legal Resources Foundation (LRF)", "legal_aid_ngo", ["Civil", "Family", "Labour"],
     "16 Oxford Road, Avondale, Harare", "08080402 (toll-free, Econet)",
     "Free legal advice and assistance from lawyers and paralegals at offices across Zimbabwe.", "free", False),
    ("Makoni & Partners", "law_firm", ["Civil", "Constitutional"],
     "Jason Moyo Ave, CBD, Harare", "+263242701234",
     "General civil and constitutional practice, pro bono slots available.", "free", False),
    ("Dube & Associates", "law_firm", ["Family", "Labour"],
     "Samora Machel Ave, Eastlea, Harare", "+263242612890",
     "Family and labour law practice.", "free", False),
    ("Moyo Chambers", "private_lawyer", ["Criminal"],
     "Joina City, CBD, Harare", "+263773456789",
     "Criminal defence practice.", "paid", False),
    ("Chirwa Legal", "private_lawyer", ["Property", "Civil"],
     "Avondale, Harare", "+263242890123",
     "Property and civil litigation.", "free", False),

    # --- from the pasted list: two shown in the browse list (they make the
    # --- Private Practitioners grid six cards, two full rows of three) ---
    ("Muvingi and Mugadza Legal Practitioners", "law_firm", [],
     "Harare", "+263242798214",
     "Legal practice in Harare. Rated 4.7 on Google (93 reviews). In business 7+ years.", "paid", False),
    ("Kanokanga & Partners", "law_firm", [],
     "Harare", "+263242781439",
     "Law firm in Harare. Rated 4.5 on Google (47 reviews). In business 35+ years.", "paid", False),

    # --- from the pasted list: found only by searching ---
    ("Sawyer and Mkushi", "law_firm", [],
     "Harare", "4796068",
     "Law firm in Harare. Rated 4.2 on Google (39 reviews). In business 10+ years.", "paid", True),
    ("Scanlen & Holderness", "law_firm", [],
     "Harare", "+263242799636",
     "Law firm in Harare. Rated 4.8 on Google (10 reviews).", "paid", True),
    ("Chiturumani Law Chambers", "law_firm", [],
     "Harare", "+263719806327",
     "Law firm in Harare. Rated 5.0 on Google (3 reviews). In business 3+ years.", "paid", True),
    ("Alex F & Associates", "law_firm", [],
     "Harare", "+263242741438",
     "Law firm. Rated 5.0 on Google (5 reviews). In business 3+ years.", "paid", True),
    ("Mhishi Nkomo Legal Practice", "law_firm", [],
     "Harare", "+263242701663",
     "Law firm in Harare. Rated 4.5 on Google (46 reviews). In business 7+ years.", "paid", True),
    ("Marume & Furidzo Legal Practitioners", "law_firm", [],
     "Harare", "+263242700609",
     "Law firm in Harare. Rated 4.0 on Google (39 reviews). In business 7+ years.", "paid", True),
    ("Wintertons Legal Practitioners", "law_firm", [],
     "Harare", "08688 007494",
     "Law firm in Harare. Rated 4.4 on Google (44 reviews). In business 120+ years.", "paid", True),
    ("Honey & Blanckenberg", "law_firm", [],
     "Harare", "+263242251331",
     "Law firm in Harare. Rated 4.6 on Google (12 reviews). In business 130+ years. Offers online appointments.", "paid", True),
    ("Nenjy Nyamapfene Law Practice", "law_firm", [],
     "Harare", "+263242751753",
     "Law firm in Harare. Rated 4.0 on Google (26 reviews). In business 15+ years.", "paid", True),
    ("Gambe Law Group (GLG)", "law_firm", [],
     "Harare", "+263242251247",
     "Law firm in Harare. Rated 5.0 on Google (12 reviews). In business 40+ years.", "paid", True),
    ("Mapendere and Partners Legal Practitioners", "private_lawyer", [],
     "Harare", "4251279",
     "Lawyer in Harare. Rated 4.7 on Google (6 reviews). In business 10+ years.", "paid", True),
    ("Madondo Legal Practice", "law_firm", [],
     "Harare", "+263773366602",
     "Law firm in Harare. Rated 4.9 on Google (31 reviews).", "paid", True),
    ("M.S Musemburi Legal Practice", "law_firm", ["Property"],
     "Harare", "+263773143982",
     "Legal practice in Harare providing property law. Rated 5.0 on Google (3 reviews).", "paid", True),
    ("Gill, Godlonton & Gerrans", "law_firm", [],
     "Harare", "+263242707023",
     "Law firm in Harare. Rated 4.3 on Google (13 reviews).", "paid", True),
    ("G. Machingambi Legal Practitioners", "law_firm", [],
     "Harare", "+263712581245",
     "Law firm in Harare. Rated 5.0 on Google (1 review).", "paid", True),
    ("Coghlan Welsh & Guest", "law_firm", ["Employment"],
     "Harare", "+263242704933",
     "Legal practice in Harare providing employment law. Rated 4.0 on Google (45 reviews). In business 10+ years.", "paid", True),
    ("Tafadzwa Ralph Mugabe | Legal Counsel", "private_lawyer", [],
     "Harare", "+263775554408",
     "Law firm in Harare. Rated 4.8 on Google (4 reviews). In business 7+ years.", "paid", True),
    # Not a law firm: the professional body for lawyers. Listed as a "law_firm"
    # only so the page has somewhere to show it -- useful for checking that a
    # lawyer is registered before paying them.
    ("Law Society of Zimbabwe", "law_firm", [],
     "Harare", "+263242751000",
     "The professional body for lawyers in Zimbabwe, not a law firm. Use it to check a lawyer's credentials.", "paid", True),
]


def _sync_directory(cur):
    """Inserts every entry in _DIRECTORY_ENTRIES that isn't in the table yet
    (matched by name). Entries already there are left exactly as they are."""
    # The state legal aid entry was called "Legal Aid Zimbabwe" in earlier
    # versions; rename it rather than adding a second copy.
    cur.execute("UPDATE directory SET name = 'Legal Aid Directorate', description = ? WHERE name = 'Legal Aid Zimbabwe' "
                "AND NOT EXISTS (SELECT 1 FROM directory WHERE name = 'Legal Aid Directorate')",
                ("Government-funded legal assistance (Ministry of Justice) for those who qualify.",))
    existing = {row["name"] for row in cur.execute("SELECT name FROM directory").fetchall()}
    cur.executemany(
        """INSERT INTO directory (name, type, focus_areas, location, contact, description, fee_basis, search_only)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(name, typ, json.dumps(areas), loc, contact, desc, fee, 1 if hidden else 0)
         for name, typ, areas, loc, contact, desc, fee, hidden in _DIRECTORY_ENTRIES
         if name not in existing]
    )


def _seed_forum(cur):
    rows = [
        ("User123", "Serving papers when defendant avoids?",
         "Has anyone dealt with a respondent who keeps dodging service? What did the messenger of court do?"),
        ("Relieved", "Success! Got protection order",
         "Just wanted to share that the process, while nerve-wracking, was manageable with the right prep. Happy to answer questions."),
    ]
    cur.executemany(
        """INSERT INTO forum_posts (author_name, title, body, created_at)
           VALUES (?, ?, ?, ?)""",
        [(a, t, b, _now()) for a, t, b in rows]
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(full_name, email, password_hash, salt):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (full_name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
        (full_name, email, password_hash, salt, _now())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


# ---------------------------------------------------------------------------
# Forum
# ---------------------------------------------------------------------------

def list_forum_posts():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM forum_posts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_forum_post(author_name, title, body):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO forum_posts (author_name, title, body, created_at) VALUES (?, ?, ?, ?)",
        (author_name, title, body, _now())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return get_forum_post(new_id)


def get_forum_post(post_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM forum_posts WHERE id = ?", (post_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Knowledge base (the chatbot's reference library -- see app/knowledge.py)
# ---------------------------------------------------------------------------

def _insert_knowledge(conn, entries):
    conn.executemany(
        "INSERT INTO knowledge (title, content, source, file_name, file_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [(e["title"], e["content"], e["source"], e["file_name"], e.get("file_key", ""), _now()) for e in entries]
    )


def replace_knowledge(entries):
    """Replaces the whole knowledge table with `entries` (a list of dicts
    with title, content, source, file_name). Used to wipe it clean; normal
    start-ups use update_knowledge() so unchanged files aren't re-read.
    """
    conn = get_connection()
    conn.execute("DELETE FROM knowledge")
    _insert_knowledge(conn, entries)
    conn.commit()
    conn.close()


def knowledge_file_keys():
    """{file_name: file_key} for every file currently loaded -- lets the
    loader tell which files are new, changed, or gone since last time."""
    conn = get_connection()
    rows = conn.execute("SELECT file_name, MIN(file_key) AS file_key FROM knowledge GROUP BY file_name").fetchall()
    conn.close()
    return {r["file_name"]: r["file_key"] for r in rows}


def update_knowledge(remove_files, entries):
    """Drops everything that came from `remove_files` (changed or deleted
    files) and adds `entries` (freshly read files), in one step."""
    conn = get_connection()
    conn.executemany("DELETE FROM knowledge WHERE file_name = ?", [(name,) for name in remove_files])
    _insert_knowledge(conn, entries)
    conn.commit()
    conn.close()


def count_knowledge():
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM knowledge").fetchone()["n"]
    conn.close()
    return n


def list_knowledge():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM knowledge ORDER BY file_name, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Case law
# ---------------------------------------------------------------------------

def create_case_law_entry(title, simplified_text, citations, court_tier="general"):
    """Saves one real Judgment Summarizer submission into the Case Law
    library, so it shows up for other users under "Case Law -- simplified
    from real submissions" (see the /simplify handler in server.py, which
    calls this after every successful simplification).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO case_law (title, simplified_text, citations, court_tier, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (title, simplified_text, json.dumps(citations), court_tier, _now())
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def list_case_law():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM case_law ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d["citations"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------

def _search_words(query):
    return re.findall(r"[a-z0-9]+", (query or "").lower())


def list_directory(query=None):
    """The directory entries to show.

    With no search, only the browse-list entries come back (search_only is
    false). With a search (`query`), EVERY entry is considered -- including the
    search-only ones -- and an entry matches when each word typed appears
    somewhere in its name, type, focus areas, location or description.
    """
    words = _search_words(query)
    conn = get_connection()
    rows = conn.execute("SELECT * FROM directory ORDER BY name COLLATE NOCASE ASC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["focus_areas"] = json.loads(d["focus_areas"])
        d["search_only"] = bool(d["search_only"])
        if words:
            haystack = " ".join([d["name"], d["type"].replace("_", " "), " ".join(d["focus_areas"]),
                                 d["location"], d["description"]]).lower()
            if not all(w in haystack for w in words):
                continue
        elif d["search_only"]:
            continue
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Legal Dictionary cache
# ---------------------------------------------------------------------------

def get_dictionary_entry(term):
    """The saved answer for `term`, or None if it hasn't been looked up yet."""
    conn = get_connection()
    row = conn.execute("SELECT data FROM dictionary_cache WHERE term = ?", (term,)).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def save_dictionary_entry(term, data):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO dictionary_cache (term, data, created_at) VALUES (?, ?, ?)",
        (term, json.dumps(data), _now())
    )
    conn.commit()
    conn.close()


def clear_dictionary_cache():
    """Forgets every saved AI answer -- e.g. after switching to a better model."""
    conn = get_connection()
    conn.execute("DELETE FROM dictionary_cache")
    conn.commit()
    conn.close()


def reset_db_for_tests():
    """Deletes the database file entirely. Only ever used by the test
    suite, so each test run starts from a clean, predictable state.
    """
    import os
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
