"""
database.py — Database setup and shared connection helper
──────────────────────────────────────────────────────────
All other modules import get_db() from here to talk to SQLite.
The init_db() function creates all tables on first run.
"""

import sqlite3
import os
import config


def get_db():
    """
    Open and return a connection to the SQLite database.
    row_factory lets us access columns by name (row['fund_name'])
    instead of by index (row[2]), which is much more readable.
    """
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # access columns by name
    conn.execute("PRAGMA journal_mode=WAL")  # safer for concurrent reads
    return conn


def init_db():
    """
    Create all database tables if they don't exist yet.
    Also runs any schema migrations needed for existing databases.
    Safe to call on every startup — won't overwrite existing data.
    """
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)

    conn = get_db()
    cursor = conn.cursor()

    # ── Table 1: files ───────────────────────────────────────────────────────
    # Full taxonomy: firm → asset_class → region → fund_name → vehicle → share_class
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            filename     TEXT NOT NULL,
            file_path    TEXT NOT NULL,
            firm_name    TEXT NOT NULL,   -- outermost level, e.g. "Acme Asset Management"
            asset_class  TEXT NOT NULL,   -- e.g. Equity, Fixed Income, Multi-Asset
            region       TEXT NOT NULL,   -- e.g. US, International, Global, Emerging Markets
            fund_name    TEXT NOT NULL,   -- strategy name, e.g. Large Cap Growth
            vehicle      TEXT,            -- e.g. Mutual Fund, LP, CIT, ETF
            share_class  TEXT,            -- e.g. Class I, Class A
            investment_style TEXT NOT NULL DEFAULT 'Not Applicable',
            -- 'Active' | 'Passive' | 'Smart Beta / Factor' | 'Not Applicable'
            data_type    TEXT NOT NULL,   -- e.g. monthly_returns, fee_schedule
            time_period  TEXT,
            access_level TEXT NOT NULL DEFAULT 'restricted',
            -- 'public'     → no permission check; anyone requesting this file gets it automatically
            --                (e.g. mutual fund factsheets, public ETF data)
            -- 'restricted' → sender must be on the approved list for this fund
            --                (e.g. CIT factsheets, LP data, SMA reports)
            upload_date  TEXT NOT NULL,
            description  TEXT,
            embedding    TEXT             -- JSON-encoded float array for semantic similarity
        )
    """)

    # ── Table 2: permissions ─────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email_address TEXT NOT NULL,
            firm_name     TEXT,           -- optional: restrict to a specific firm
            fund_name     TEXT NOT NULL,
            vehicle       TEXT,
            share_class   TEXT,
            added_date    TEXT NOT NULL
        )
    """)

    # ── Table 3: requests ────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_email     TEXT NOT NULL,
            subject          TEXT,
            body             TEXT,
            received_at      TEXT NOT NULL,

            -- What Claude extracted from the email
            parsed_firm      TEXT,
            parsed_fund      TEXT,
            parsed_vehicle   TEXT,
            parsed_data_type TEXT,
            parsed_period    TEXT,
            parse_confidence TEXT,
            parse_summary    TEXT,

            -- Outcome
            status           TEXT NOT NULL DEFAULT 'pending',
            -- 'pending'   → being processed
            -- 'auto_sent' → sent automatically (high confidence)
            -- 'forwarded' → forwarded to consultant (uncertain)
            -- 'reviewed'  → human marked as handled

            matched_file_id  INTEGER,
            draft_id         TEXT,     -- stores sent/forwarded message ID
            flag_reason      TEXT,
            handled_at       TEXT,
            notes            TEXT,

            FOREIGN KEY (matched_file_id) REFERENCES files(id)
        )
    """)

    # ── Table 4: strategies ──────────────────────────────────────────────────────
    # Permanent record of every strategy ever uploaded — survives file deletion.
    # Hierarchy: firm → investment_style → asset_class → region → fund_name → vehicle
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_name        TEXT NOT NULL,
            investment_style TEXT NOT NULL DEFAULT 'Not Applicable',
            asset_class      TEXT NOT NULL,
            region           TEXT NOT NULL,
            fund_name        TEXT NOT NULL,
            vehicle          TEXT NOT NULL DEFAULT '',
            share_class      TEXT NOT NULL DEFAULT '',
            created_date     TEXT NOT NULL,
            UNIQUE(firm_name, investment_style, asset_class, region, fund_name, vehicle, share_class)
        )
    """)

    # ── Table 5: cr_regions ──────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cr_regions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            region_name  TEXT NOT NULL UNIQUE,
            created_date TEXT NOT NULL
        )
    """)

    # ── Table 6: cr_assignments ──────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cr_assignments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            region_name  TEXT NOT NULL,
            member_name  TEXT NOT NULL,
            member_email TEXT NOT NULL,
            created_date TEXT NOT NULL,
            UNIQUE(region_name, member_email)
        )
    """)

    # ── Table 7: sender_profiles ─────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sender_profiles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email_address TEXT NOT NULL UNIQUE,
            region_name   TEXT NOT NULL,
            created_date  TEXT NOT NULL
        )
    """)

    # ── Table 8: users ───────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            email                TEXT    NOT NULL UNIQUE,
            name                 TEXT    NOT NULL,
            password_hash        TEXT    NOT NULL,
            role                 TEXT    NOT NULL DEFAULT 'cr_member',
            is_active            INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_date         TEXT    NOT NULL,
            last_login           TEXT
        )
    """)

    conn.commit()

    # ── Migrations: add new columns to existing databases without losing data ──
    file_cols = [row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()]
    _add_column_if_missing(conn, "files", "firm_name",    "TEXT NOT NULL DEFAULT ''",          file_cols)
    _add_column_if_missing(conn, "files", "asset_class",  "TEXT NOT NULL DEFAULT ''",          file_cols)
    _add_column_if_missing(conn, "files", "region",       "TEXT NOT NULL DEFAULT ''",          file_cols)
    _add_column_if_missing(conn, "files", "vehicle",      "TEXT",                              file_cols)
    _add_column_if_missing(conn, "files", "share_class",  "TEXT",                              file_cols)
    _add_column_if_missing(conn, "files", "access_level",      "TEXT NOT NULL DEFAULT 'restricted'",     file_cols)
    _add_column_if_missing(conn, "files", "investment_style",  "TEXT NOT NULL DEFAULT 'Not Applicable'",  file_cols)

    perm_cols = [row[1] for row in conn.execute("PRAGMA table_info(permissions)").fetchall()]
    _add_column_if_missing(conn, "permissions", "firm_name",   "TEXT", perm_cols)
    _add_column_if_missing(conn, "permissions", "vehicle",     "TEXT", perm_cols)
    _add_column_if_missing(conn, "permissions", "share_class", "TEXT", perm_cols)
    _add_column_if_missing(conn, "permissions", "granted_by",  "TEXT", perm_cols)

    req_cols = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
    _add_column_if_missing(conn, "requests", "parsed_firm",               "TEXT", req_cols)
    _add_column_if_missing(conn, "requests", "assigned_to",               "TEXT", req_cols)
    _add_column_if_missing(conn, "requests", "clarification_thread_id",   "TEXT", req_cols)

    file_cols2 = [row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()]
    _add_column_if_missing(conn, "files", "update_cadence",   "TEXT", file_cols2)
    _add_column_if_missing(conn, "files", "next_update_date", "TEXT", file_cols2)
    conn.commit()

    file_cols3 = [row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()]
    _add_column_if_missing(conn, "files", "superseded_by", "INTEGER", file_cols3)
    conn.commit()

    user_cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    _add_column_if_missing(conn, "users", "is_active",            "INTEGER NOT NULL DEFAULT 1", user_cols)
    _add_column_if_missing(conn, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0", user_cols)
    _add_column_if_missing(conn, "users", "last_login",           "TEXT", user_cols)
    conn.commit()

    conn.close()
    print("[DB] Database initialized successfully.")


def seed_admin_from_env():
    """
    On first run, create an admin user from ADMIN_PASSWORD + ADMIN_EMAIL env vars.
    Idempotent — does nothing if any active admin row already exists.
    Called from app.py after init_db().
    """
    from werkzeug.security import generate_password_hash

    admin_password = config.ADMIN_PASSWORD
    admin_email    = config.ADMIN_EMAIL

    if not admin_password or not admin_email:
        return  # auth not configured or email not set

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchone()
    if existing:
        conn.close()
        return  # already seeded

    import datetime
    conn.execute(
        """
        INSERT INTO users (email, name, password_hash, role, is_active,
                           must_change_password, created_date)
        VALUES (?, 'Admin', ?, 'admin', 1, 0, ?)
        """,
        (admin_email.lower().strip(),
         generate_password_hash(admin_password),
         datetime.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    print(f"[DB] Seeded initial admin account: {admin_email}")


def _add_column_if_missing(conn, table, column, col_type, existing_columns):
    """Helper: add a column to a table only if it doesn't already exist."""
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        print(f"[DB] Migration: added '{column}' to {table}.")
