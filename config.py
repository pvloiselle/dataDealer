"""
config.py — Central configuration for DataDealer
─────────────────────────────────────────────────
Reads all settings from the .env file so that secrets (API keys, etc.)
never have to be hard-coded in the source code.
"""

import os
from dotenv import load_dotenv

# Load the .env file from the project root
load_dotenv()


# ── Anthropic (Claude AI) ────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Gmail ────────────────────────────────────────────────────────────────────
GMAIL_INBOX_EMAIL = os.getenv("GMAIL_INBOX_EMAIL")
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials/credentials.json")
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "credentials/token.json")

# OAuth scopes:
#   - gmail.readonly  → read incoming emails
#   - gmail.compose   → create drafts (intentionally NOT gmail.send, so nothing auto-sends)
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",  # needed to mark emails as read
]

# ── Storage ──────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/datadealer.db")

# ── Scheduling ───────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

# ── Semantic Search ──────────────────────────────────────────────────────────
# Cosine similarity score that a file must exceed to be auto-matched.
# Range: 0.0 (match anything) to 1.0 (exact match only).
# 0.65 is a sensible default for this kind of metadata matching.
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))

# ── Flask ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# Allowed file types for upload
ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xls", "csv", "docx", "pptx"}

# Maximum file size: 50 MB
MAX_CONTENT_LENGTH = 50 * 1024 * 1024
