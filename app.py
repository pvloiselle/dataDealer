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

import os
from flask import Flask

import config
from modules.database import init_db
from modules.dashboard import bp as dashboard_blueprint
from scheduler import init_scheduler


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

    # ── Ensure required folders exist ────────────────────────────────────────
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs("credentials", exist_ok=True)
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)

    # ── Initialize the database ───────────────────────────────────────────────
    # Creates tables if they don't exist. Safe to call every startup.
    with app.app_context():
        init_db()

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
