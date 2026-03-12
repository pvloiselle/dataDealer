"""
scheduler.py — Background email polling job
─────────────────────────────────────────────
Sets up Flask-APScheduler to call poll_and_process_inbox() every N minutes.
The scheduler runs in the same process as Flask — no separate worker needed.

The job is defined here and registered onto the Flask app in app.py.
"""

from flask_apscheduler import APScheduler

# Create the scheduler instance
scheduler = APScheduler()


def init_scheduler(app):
    """
    Attach the scheduler to the Flask app and register the polling job.
    Called once at startup from app.py.
    """
    import config

    scheduler.init_app(app)

    # Register the polling job:
    # interval trigger = run every N minutes
    scheduler.add_job(
        id="poll_gmail_inbox",              # Unique job ID
        func=_poll_job,                     # The function to call
        trigger="interval",                 # Run on a repeating interval
        minutes=config.POLL_INTERVAL_MINUTES,
        replace_existing=True,              # Don't create duplicates on restart
    )

    scheduler.start()
    print(f"[Scheduler] Gmail polling started — every {config.POLL_INTERVAL_MINUTES} minute(s).")


def _poll_job():
    """
    The actual job function called by APScheduler.
    Imports request_processor here (inside the function) to avoid circular imports.
    """
    from modules.request_processor import poll_and_process_inbox
    poll_and_process_inbox()
