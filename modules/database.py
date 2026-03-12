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
    Safe to call on every startup — won't overwrite existing data.
    """
    # Make sure the database folder exists
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)

    conn = get_db()
    cursor = conn.cursor()

    # ── Table 1: files ───────────────────────────────────────────────────────
    # Stores metadata for every file uploaded by the admin.
    # The 'embedding' column holds a JSON array of floats used for semantic search.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            fund_name   TEXT NOT NULL,
            vehicle_name TEXT,
            data_type   TEXT NOT NULL,
            time_period TEXT,
            upload_date TEXT NOT NULL,
            description TEXT,
            embedding   TEXT    -- JSON-encoded float array for semantic similarity
        )
    """)

    # ── Table 2: permissions ─────────────────────────────────────────────────
    # Maps approved email addresses to specific fund/vehicle combinations.
    # A sender is only auto-fulfilled if their email appears here for the
    # fund that was requested.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email_address TEXT NOT NULL,
            fund_name    TEXT NOT NULL,
            vehicle_name TEXT,
            added_date   TEXT NOT NULL
        )
    """)

    # ── Table 3: requests ────────────────────────────────────────────────────
    # Every incoming email is logged here, whether auto-fulfilled or flagged.
    # This is the audit trail.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_email    TEXT NOT NULL,
            subject         TEXT,
            body            TEXT,
            received_at     TEXT NOT NULL,

            -- What Claude extracted from the email
            parsed_fund     TEXT,
            parsed_vehicle  TEXT,
            parsed_data_type TEXT,
            parsed_period   TEXT,
            parse_confidence TEXT,
            parse_summary   TEXT,

            -- Outcome
            status          TEXT NOT NULL DEFAULT 'pending',
            -- 'pending'        → just received, being processed
            -- 'auto_fulfilled' → draft created, awaiting human send
            -- 'flagged'        → needs human review (not approved / no match)
            -- 'reviewed'       → human marked as handled

            matched_file_id INTEGER,  -- FK to files.id if a match was found
            draft_id        TEXT,     -- Gmail draft ID if a draft was created
            flag_reason     TEXT,     -- Why it was flagged, if applicable
            handled_at      TEXT,     -- When a human marked it reviewed
            notes           TEXT,     -- Admin notes on reviewed items

            FOREIGN KEY (matched_file_id) REFERENCES files(id)
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Database initialized successfully.")
