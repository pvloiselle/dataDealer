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

# The email address of the human consultant who receives forwarded uncertain requests.
# All uncertain/unapproved requests will be emailed here with full context.
CONSULTANT_EMAIL = os.getenv("CONSULTANT_EMAIL")

# OAuth scopes:
#   - gmail.readonly  → read incoming emails
#   - gmail.send      → send auto-responses for high-confidence matches
#   - gmail.modify    → mark emails as read
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

# ── Storage ──────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/datadealer.db")

# ── Scheduling ───────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

# ── Semantic Search ──────────────────────────────────────────────────────────
# SIMILARITY_THRESHOLD: minimum score for a file to be considered a candidate match at all.
# Requests below this score are forwarded with "no file found" context.
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))

# HIGH_CONFIDENCE_THRESHOLD: score at which a match is considered confident enough to auto-send.
# Must also be an approved sender with Claude parse confidence = "high".
# Raise this to be more conservative; lower it to auto-send more aggressively.
HIGH_CONFIDENCE_THRESHOLD = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.82"))

# ── Flask ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# Allowed file types for upload
ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xls", "csv", "docx", "pptx"}

# Maximum file size: 50 MB
MAX_CONTENT_LENGTH = 50 * 1024 * 1024

# Number of grace period days after the expected next update date before marking a file stale
FRESHNESS_GRACE_PERIOD_DAYS = int(os.getenv("FRESHNESS_GRACE_PERIOD_DAYS", "14"))

# ── Admin Auth ────────────────────────────────────────────────────────────────
# Password required to perform sensitive admin actions (upload, permissions, config).
# Set via environment variable. If empty, admin auth is disabled (dev convenience).
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Email address for the initial admin account seeded on first startup.
# Only used once — after the users table has an active admin, this is ignored.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

# ── Notifications ─────────────────────────────────────────────────────────────
# Email address to notify when a request enters the pending_clarification queue.
# Leave blank to disable queue notifications.
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", "")
