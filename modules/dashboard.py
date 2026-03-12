"""
dashboard.py — All Flask web routes (the admin UI)
────────────────────────────────────────────────────
This is a Flask "Blueprint" — a self-contained set of routes that gets
registered onto the main app in app.py.

Routes:
  GET  /                      → Dashboard overview (stats)
  GET  /upload                → File upload form
  POST /upload                → Handle file upload submission
  POST /files/<id>/delete     → Delete a file
  GET  /permissions           → Manage approved email addresses
  POST /permissions/add       → Add a new permission
  POST /permissions/<id>/remove → Remove a permission
  GET  /log                   → Full request log
  GET  /review                → Queue of flagged requests needing human review
  POST /review/<id>/handled   → Mark a flagged request as reviewed/handled
"""

import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash

from modules import file_manager, permissions
from modules.database import get_db

# Create the Blueprint — all routes below are registered on this object
bp = Blueprint("dashboard", __name__)


# ── Overview Dashboard ────────────────────────────────────────────────────────

@bp.route("/")
def index():
    """Main dashboard page — shows summary statistics."""
    conn = get_db()

    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_requests = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    auto_fulfilled = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE status = 'auto_fulfilled'"
    ).fetchone()[0]
    flagged_pending = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE status = 'flagged'"
    ).fetchone()[0]
    total_permissions = conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]

    # Recent requests for the mini-log on the dashboard
    recent_requests = conn.execute(
        """
        SELECT sender_email, subject, status, received_at, parse_summary
        FROM requests ORDER BY received_at DESC LIMIT 10
        """
    ).fetchall()

    conn.close()

    return render_template(
        "index.html",
        total_files=total_files,
        total_requests=total_requests,
        auto_fulfilled=auto_fulfilled,
        flagged_pending=flagged_pending,
        total_permissions=total_permissions,
        recent_requests=recent_requests,
    )


# ── File Upload ───────────────────────────────────────────────────────────────

@bp.route("/upload")
def upload():
    """Show the file upload form and list of existing files."""
    files = file_manager.get_all_files()
    return render_template("upload.html", files=files)


@bp.route("/upload", methods=["POST"])
def upload_post():
    """Handle a file upload form submission."""
    uploaded_file = request.files.get("file")
    fund_name = request.form.get("fund_name", "").strip()
    vehicle_name = request.form.get("vehicle_name", "").strip()
    data_type = request.form.get("data_type", "").strip()
    time_period = request.form.get("time_period", "").strip()
    description = request.form.get("description", "").strip()

    # Validate required fields
    if not uploaded_file or uploaded_file.filename == "":
        flash("Please select a file to upload.", "error")
        return redirect(url_for("dashboard.upload"))

    if not fund_name:
        flash("Fund name is required.", "error")
        return redirect(url_for("dashboard.upload"))

    if not data_type:
        flash("Data type is required.", "error")
        return redirect(url_for("dashboard.upload"))

    try:
        result = file_manager.save_file(
            file=uploaded_file,
            fund_name=fund_name,
            vehicle_name=vehicle_name,
            data_type=data_type,
            time_period=time_period,
            description=description,
        )
        flash(f"File '{result['filename']}' uploaded and indexed successfully.", "success")
    except ValueError as e:
        flash(str(e), "error")
    except Exception as e:
        flash(f"Upload failed: {e}", "error")

    return redirect(url_for("dashboard.upload"))


@bp.route("/files/<int:file_id>/delete", methods=["POST"])
def delete_file(file_id):
    """Delete a file from the database and disk."""
    deleted = file_manager.delete_file(file_id)
    if deleted:
        flash("File deleted successfully.", "success")
    else:
        flash("File not found.", "error")
    return redirect(url_for("dashboard.upload"))


# ── Permissions ───────────────────────────────────────────────────────────────

@bp.route("/permissions")
def permissions_page():
    """Show the permissions management page."""
    all_perms = permissions.get_all_permissions()

    # Get distinct fund names from the files table for the dropdown
    conn = get_db()
    funds = conn.execute("SELECT DISTINCT fund_name FROM files ORDER BY fund_name").fetchall()
    conn.close()
    fund_names = [f["fund_name"] for f in funds]

    return render_template("permissions.html", permissions=all_perms, fund_names=fund_names)


@bp.route("/permissions/add", methods=["POST"])
def add_permission():
    """Add a new approved email → fund/vehicle mapping."""
    email_address = request.form.get("email_address", "").strip()
    fund_name = request.form.get("fund_name", "").strip()
    vehicle_name = request.form.get("vehicle_name", "").strip()

    if not email_address or not fund_name:
        flash("Email address and fund name are required.", "error")
        return redirect(url_for("dashboard.permissions_page"))

    permissions.add_permission(email_address, fund_name, vehicle_name)
    flash(
        f"Added permission: {email_address} → {fund_name}"
        + (f" / {vehicle_name}" if vehicle_name else " (all vehicles)"),
        "success",
    )
    return redirect(url_for("dashboard.permissions_page"))


@bp.route("/permissions/<int:perm_id>/remove", methods=["POST"])
def remove_permission(perm_id):
    """Remove a permission by ID."""
    deleted = permissions.remove_permission(perm_id)
    if deleted:
        flash("Permission removed.", "success")
    else:
        flash("Permission not found.", "error")
    return redirect(url_for("dashboard.permissions_page"))


# ── Request Log ───────────────────────────────────────────────────────────────

@bp.route("/log")
def log():
    """Show the full request log."""
    conn = get_db()
    all_requests = conn.execute(
        """
        SELECT r.*, f.filename as matched_filename
        FROM requests r
        LEFT JOIN files f ON r.matched_file_id = f.id
        ORDER BY r.received_at DESC
        """
    ).fetchall()
    conn.close()
    return render_template("log.html", requests=all_requests)


# ── Review Queue ──────────────────────────────────────────────────────────────

@bp.route("/review")
def review():
    """Show the queue of flagged requests waiting for human review."""
    conn = get_db()
    flagged = conn.execute(
        """
        SELECT r.*, f.filename as matched_filename, f.file_path as matched_file_path
        FROM requests r
        LEFT JOIN files f ON r.matched_file_id = f.id
        WHERE r.status = 'flagged'
        ORDER BY r.received_at DESC
        """
    ).fetchall()
    conn.close()
    return render_template("review.html", flagged=flagged)


@bp.route("/review/<int:request_id>/handled", methods=["POST"])
def mark_handled(request_id):
    """Mark a flagged request as reviewed/handled by a human."""
    notes = request.form.get("notes", "").strip()
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET status = 'reviewed', handled_at = ?, notes = ?
        WHERE id = ?
        """,
        (datetime.datetime.now().isoformat(), notes, request_id),
    )
    conn.commit()
    conn.close()
    flash("Request marked as handled.", "success")
    return redirect(url_for("dashboard.review"))
