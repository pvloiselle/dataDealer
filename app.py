"""
app.py — DataDealer Flask application entry point
──────────────────────────────────────────────────
This file does three things:
  1. Creates and configures the Flask app
  2. Initializes the SQLite database on first run
  3. Registers the dashboard routes and starts the background email poller

To run the application:
    python app.py
Then open http://localhost:5001 in your browser.
"""

import base64
import datetime
import os
from flask import Flask

import config
from extensions import csrf, limiter
from modules.database import init_db
from modules.dashboard import bp as dashboard_blueprint
from scheduler import init_scheduler


def _bootstrap_credentials():
    """Write credential files from env vars if they don't exist on disk.

    On Railway (and other PaaS platforms), credential files can't be committed
    to git, so they're stored as base64-encoded environment variables and
    written to the persistent volume path on first startup.
    Silent no-op if env vars are absent (preserves local dev behavior).
    """
    pairs = [
        ("GMAIL_TOKEN_JSON",       config.GMAIL_TOKEN_FILE),
        ("GMAIL_CREDENTIALS_JSON", config.GMAIL_CREDENTIALS_FILE),
    ]
    for env_var, file_path in pairs:
        encoded = os.getenv(env_var)
        if encoded and not os.path.exists(file_path):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(base64.b64decode(encoded))
            print(f"[Startup] Wrote {file_path} from environment variable.")


def create_app():
    """
    Flask application factory — creates and configures the app.
    Keeping setup in a function (rather than at module level) makes it
    easier to test and avoids side effects on import.
    """
    app = Flask(__name__)

    # ── Flask configuration ───────────────────────────────────────────────────
    app.secret_key = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
    app.config["UPLOAD_FOLDER"] = config.UPLOAD_FOLDER

    # Required by Flask-APScheduler to avoid duplicate jobs in debug mode
    app.config["SCHEDULER_API_ENABLED"] = False

    # CSRF — protect all forms; exempt JSON-only API endpoints individually
    app.config["WTF_CSRF_ENABLED"] = True
    csrf.init_app(app)

    # Rate limiter — storage uses in-memory by default (fine for single-instance)
    limiter.init_app(app)

    # Sessions expire after 8 hours of inactivity
    app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(hours=8)

    # ── Security headers ──────────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
        # Allows Bootstrap/Google Fonts CDN plus our own origin; blocks everything else
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return response

    # ── Bootstrap credentials from env vars (Railway / cloud deploy) ─────────
    _bootstrap_credentials()

    # ── Ensure required folders exist ────────────────────────────────────────
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.dirname(config.GMAIL_TOKEN_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)

    # ── Initialize the database ───────────────────────────────────────────────
    # Creates tables if they don't exist. Safe to call every startup.
    with app.app_context():
        init_db()
        from modules.database import seed_admin_from_env
        seed_admin_from_env()

    # ── Register routes ───────────────────────────────────────────────────────
    # All UI routes are defined in modules/dashboard.py
    app.register_blueprint(dashboard_blueprint)

    # ── Start background email polling ────────────────────────────────────────
    # This starts polling Gmail every 5 minutes (configurable in .env).
    # We check for the Werkzeug reloader: in debug mode, Flask runs two processes
    # and we only want the scheduler running in one of them.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        init_scheduler(app)

    return app


# ── Run the app ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    print("\n" + "=" * 60)
    print("  DataDealer is running!")
    print("  Open your browser and go to: http://localhost:5001")
    print("=" * 60 + "\n")
    # debug=False in a real deployment; True shows detailed errors during development
    app.run(host="0.0.0.0", port=5001, debug=False)
