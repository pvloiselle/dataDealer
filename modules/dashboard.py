"""
dashboard.py — All Flask web routes (the admin UI)
────────────────────────────────────────────────────
Routes:
  GET/POST /admin/login           → Login page (admin + CR members)
  POST     /admin/logout          → Log out
  GET      /                      → Dashboard overview (role-scoped)
  GET      /upload                → File upload form
  POST     /upload                → Handle file upload submission
  POST     /files/<id>/delete     → Delete a file
  GET      /permissions           → Manage approved email addresses
  POST     /permissions/add       → Add a new permission
  POST     /permissions/<id>/remove → Remove a permission
  GET      /log                   → Full request log (role-scoped)
  GET      /review                → Queue of forwarded requests (role-scoped)
  POST     /review/<id>/handled   → Mark a request as reviewed
  GET      /strategies            → Strategy browser
  GET      /config                → CR routing configuration
  GET      /admin/users           → User account management
  POST     /admin/users/create    → Create a new user
  POST     /admin/users/<id>/deactivate  → Deactivate a user
  POST     /admin/users/<id>/reactivate  → Reactivate a user
  POST     /admin/users/<id>/reset-password → Admin resets a user's password
  GET/POST /account/change-password → Self-service password change
"""

import datetime
import functools
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import limiter, csrf

import config
from modules import file_manager, permissions
from modules.database import get_db

bp = Blueprint("dashboard", __name__)


# ── Auth Decorators ────────────────────────────────────────────────────────────

def require_login(f):
    """Any authenticated user (admin or cr_member) passes. Unauthenticated → login."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not config.ADMIN_PASSWORD:
            return f(*args, **kwargs)  # auth disabled (dev mode)
        if session.get("user_role") in ("admin", "cr_member"):
            return f(*args, **kwargs)
        wants_json = (
            request.is_json
            or request.headers.get("Accept", "").startswith("application/json")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({"error": "Authentication required.", "auth_required": True}), 403
        return redirect(url_for("dashboard.login", next=request.url))
    return decorated


def require_admin(f):
    """Admin role only. CR members get a flash error + redirect. Unauthenticated → login."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not config.ADMIN_PASSWORD:
            return f(*args, **kwargs)  # auth disabled (dev mode)
        if session.get("user_role") == "admin":
            return f(*args, **kwargs)
        wants_json = (
            request.is_json
            or request.headers.get("Accept", "").startswith("application/json")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if session.get("user_role") == "cr_member":
            if wants_json:
                return jsonify({"error": "Admin access required.", "auth_required": False}), 403
            flash("This page is restricted to administrators.", "error")
            return redirect(url_for("dashboard.index"))
        if wants_json:
            return jsonify({"error": "Admin authentication required.", "auth_required": True}), 403
        return redirect(url_for("dashboard.login", next=request.url))
    return decorated


# ── Login / Logout ─────────────────────────────────────────────────────────────

@bp.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 30 per hour")
def login():
    """Login page — used by both admins and CR members."""
    if session.get("user_role"):
        return redirect(url_for("dashboard.index"))

    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? AND is_active = 1", (email,)
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session.permanent = True  # honour PERMANENT_SESSION_LIFETIME
            session["user_id"]    = user["id"]
            session["user_email"] = user["email"]
            session["user_name"]  = user["name"]
            session["user_role"]  = user["role"]
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.datetime.now().isoformat(), user["id"])
            )
            conn.commit()
            conn.close()
            next_url = request.form.get("next") or url_for("dashboard.index")
            if not next_url.startswith("/"):
                next_url = url_for("dashboard.index")
            return redirect(next_url)

        conn.close()
        error = "Incorrect email or password."

    next_url = request.args.get("next", "")
    return render_template("admin_login.html", error=error, next_url=next_url)


@bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    """Log out — clears all session keys."""
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("dashboard.login"))


# ── Overview Dashboard ─────────────────────────────────────────────────────────

@bp.route("/")
@require_login
def index():
    """Main dashboard — stats scoped by role."""
    conn   = get_db()
    role   = session.get("user_role")
    email  = session.get("user_email")

    if role == "admin":
        total_requests    = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        auto_fulfilled    = conn.execute("SELECT COUNT(*) FROM requests WHERE status='auto_sent'").fetchone()[0]
        forwarded_count   = conn.execute("SELECT COUNT(*) FROM requests WHERE status='forwarded'").fetchone()[0]
        total_files       = conn.execute("SELECT COUNT(*) FROM files WHERE superseded_by IS NULL").fetchone()[0]
        total_permissions = conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
        resolved_count    = None
        recent_requests   = conn.execute(
            "SELECT sender_email, subject, status, received_at, parse_summary "
            "FROM requests ORDER BY received_at DESC LIMIT 10"
        ).fetchall()
    else:
        total_requests  = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE assigned_to = ?", (email,)
        ).fetchone()[0]
        forwarded_count = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE assigned_to = ? "
            "AND status IN ('forwarded', 'flagged', 'pending_clarification')", (email,)
        ).fetchone()[0]
        resolved_count  = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE assigned_to = ? AND status = 'reviewed'", (email,)
        ).fetchone()[0]
        auto_fulfilled    = None
        total_files       = None
        total_permissions = None
        recent_requests   = conn.execute(
            "SELECT sender_email, subject, status, received_at, parse_summary "
            "FROM requests WHERE assigned_to = ? ORDER BY received_at DESC LIMIT 10",
            (email,)
        ).fetchall()

    conn.close()

    from modules.file_manager import get_stale_files
    stale_files = get_stale_files() if role == "admin" else []

    must_change_password = False
    if role == "cr_member":
        conn2 = get_db()
        flag = conn2.execute(
            "SELECT must_change_password FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        must_change_password = bool(flag and flag["must_change_password"])
        conn2.close()

    return render_template(
        "index.html",
        total_files=total_files,
        total_requests=total_requests,
        auto_fulfilled=auto_fulfilled,
        forwarded_count=forwarded_count,
        resolved_count=resolved_count,
        total_permissions=total_permissions,
        recent_requests=recent_requests,
        stale_files=stale_files,
        stale_count=len(stale_files),
        must_change_password=must_change_password,
    )


# ── File Upload ────────────────────────────────────────────────────────────────

@bp.route("/upload")
@require_admin
def upload():
    """Show the file upload form and list of existing files."""
    files = file_manager.get_all_files()
    conn = get_db()
    firm_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT firm_name FROM files WHERE firm_name != '' ORDER BY firm_name"
    ).fetchall()]
    conn.close()
    prefill = {
        "firm_name":        request.args.get("firm", ""),
        "investment_style": request.args.get("style", ""),
        "asset_class":      request.args.get("asset_class", ""),
        "region":           request.args.get("region", ""),
        "fund_name":        request.args.get("fund", ""),
        "vehicle":          request.args.get("vehicle", ""),
        "share_class":      request.args.get("share_class", ""),
        "update_cadence":   request.args.get("update_cadence", ""),
    }
    from_strategy = any(prefill.values())
    return render_template("upload.html", files=files, firm_names=firm_names,
                           prefill=prefill, from_strategy=from_strategy)


@bp.route("/upload", methods=["POST"])
@require_admin
def upload_post():
    """Handle a file upload form submission."""
    uploaded_file    = request.files.get("file")
    firm_name        = request.form.get("firm_name", "").strip()
    asset_class      = request.form.get("asset_class", "").strip()
    region           = request.form.get("region", "").strip()
    fund_name        = request.form.get("fund_name", "").strip()
    vehicle          = request.form.get("vehicle", "").strip()
    share_class      = request.form.get("share_class", "").strip()
    investment_style = request.form.get("investment_style", "Not Applicable").strip()
    data_type        = request.form.get("data_type", "").strip()
    time_period      = request.form.get("time_period", "").strip()
    access_level     = request.form.get("access_level", "restricted").strip()
    description      = request.form.get("description", "").strip()
    update_cadence   = request.form.get("update_cadence", "").strip()
    raw              = request.form.get("supersede_file_id", "").strip()
    supersede_file_id = int(raw) if raw.isdigit() else None
    return_to        = request.form.get("_return_to", "upload")

    if not uploaded_file or uploaded_file.filename == "":
        flash("Please select a file to upload.", "error")
        return redirect(url_for("dashboard.upload"))
    if not firm_name:
        flash("Firm name is required.", "error")
        return redirect(url_for("dashboard.upload"))
    if not asset_class:
        flash("Asset class is required.", "error")
        return redirect(url_for("dashboard.upload"))
    if not region:
        flash("Region is required.", "error")
        return redirect(url_for("dashboard.upload"))
    if not fund_name:
        flash("Strategy / fund name is required.", "error")
        return redirect(url_for("dashboard.upload"))
    if not data_type:
        flash("Data type is required.", "error")
        return redirect(url_for("dashboard.upload"))

    try:
        result = file_manager.save_file(
            file=uploaded_file,
            firm_name=firm_name,
            asset_class=asset_class,
            region=region,
            fund_name=fund_name,
            vehicle=vehicle,
            share_class=share_class,
            investment_style=investment_style,
            data_type=data_type,
            time_period=time_period,
            access_level=access_level,
            description=description,
            update_cadence=update_cadence,
            supersede_file_id=supersede_file_id,
        )
        flash(f"File '{result['filename']}' uploaded and indexed successfully.", "success")
    except ValueError as e:
        flash(str(e), "error")
    except Exception as e:
        flash(f"Upload failed: {e}", "error")

    if return_to == "strategies":
        return redirect(url_for("dashboard.strategies"))
    return redirect(url_for("dashboard.upload"))


@bp.route("/upload/analyze", methods=["POST"])
@require_admin
@csrf.exempt
def upload_analyze():
    """AI-assisted metadata suggestion endpoint."""
    uploaded_file = request.files.get("file")
    if not uploaded_file or uploaded_file.filename == "":
        return jsonify({}), 400

    filename   = uploaded_file.filename
    file_bytes = uploaded_file.read()

    try:
        from modules import ai_analyzer
        suggestions = ai_analyzer.analyze_file_for_metadata(filename, file_bytes)
        return jsonify(suggestions)
    except Exception as e:
        import traceback
        print(f"[Analyzer] Unhandled error in upload_analyze: {e}")
        traceback.print_exc()
        return jsonify({}), 500


@bp.route("/upload/check-duplicate")
@require_admin
def upload_check_duplicate():
    """Return JSON list of active files matching the given firm/fund/data_type."""
    firm      = request.args.get("firm", "").strip()
    fund      = request.args.get("fund", "").strip()
    data_type = request.args.get("data_type", "").strip()

    if not firm and not fund and not data_type:
        return jsonify([])

    conn   = get_db()
    query  = "SELECT id, filename, upload_date, time_period FROM files WHERE superseded_by IS NULL"
    params = []
    if firm:
        query += " AND firm_name = ?"
        params.append(firm)
    if fund:
        query += " AND fund_name = ?"
        params.append(fund)
    if data_type:
        query += " AND data_type = ?"
        params.append(data_type)
    query += " ORDER BY upload_date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route("/files/<int:file_id>/delete", methods=["POST"])
@require_admin
def delete_file(file_id):
    """Delete a file from the database and disk."""
    deleted = file_manager.delete_file(file_id)
    if deleted:
        flash("File deleted successfully.", "success")
    else:
        flash("File not found.", "error")
    return redirect(url_for("dashboard.upload"))


# ── Permissions ────────────────────────────────────────────────────────────────

@bp.route("/permissions")
@require_admin
def permissions_page():
    """Show the permissions management page."""
    all_perms = permissions.get_all_permissions()

    conn = get_db()
    fund_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT fund_name FROM files ORDER BY fund_name"
    ).fetchall()]
    firm_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT firm_name FROM files WHERE firm_name != '' ORDER BY firm_name"
    ).fetchall()]
    conn.close()

    return render_template("permissions.html", permissions=all_perms,
                           fund_names=fund_names, firm_names=firm_names)


@bp.route("/permissions/add", methods=["POST"])
@require_admin
def add_permission():
    """Add a new approved email → firm/fund/vehicle/share class mapping."""
    email_address = request.form.get("email_address", "").strip()
    firm_name     = request.form.get("firm_name", "").strip()
    fund_name     = request.form.get("fund_name", "").strip()
    vehicle       = request.form.get("vehicle", "").strip()
    share_class   = request.form.get("share_class", "").strip()
    granted_by    = request.form.get("granted_by", "").strip()

    if not email_address or not fund_name:
        flash("Email address and fund name are required.", "error")
        return redirect(url_for("dashboard.permissions_page"))
    if not granted_by:
        flash("Your email is required so ownership of this permission can be recorded.", "error")
        return redirect(url_for("dashboard.permissions_page"))

    permissions.add_permission(email_address, firm_name, fund_name, vehicle, share_class, granted_by)
    label = f"{firm_name} / {fund_name}" if firm_name else fund_name
    if vehicle:
        label += f" / {vehicle}"
    if share_class:
        label += f" / {share_class}"
    flash(f"Added permission: {email_address} → {label}", "success")
    return redirect(url_for("dashboard.permissions_page"))


@bp.route("/permissions/<int:perm_id>/remove", methods=["POST"])
@require_admin
def remove_permission(perm_id):
    """Remove a permission by ID. Requires the granter's email to confirm ownership."""
    confirm_email = request.form.get("confirm_email", "").strip()
    success, reason = permissions.remove_permission(perm_id, confirm_email)
    if success:
        flash("Permission removed.", "success")
    else:
        flash(reason, "error")
    return redirect(url_for("dashboard.permissions_page"))


# ── Request Log ────────────────────────────────────────────────────────────────

@bp.route("/log")
@require_login
def log():
    """Show the full request log, scoped by role."""
    conn  = get_db()
    role  = session.get("user_role")
    email = session.get("user_email")

    if role == "admin":
        all_requests = conn.execute(
            "SELECT r.*, f.filename as matched_filename "
            "FROM requests r LEFT JOIN files f ON r.matched_file_id = f.id "
            "ORDER BY r.received_at DESC"
        ).fetchall()
    else:
        all_requests = conn.execute(
            "SELECT r.*, f.filename as matched_filename "
            "FROM requests r LEFT JOIN files f ON r.matched_file_id = f.id "
            "WHERE r.assigned_to = ? "
            "ORDER BY r.received_at DESC",
            (email,)
        ).fetchall()

    conn.close()
    return render_template("log.html", requests=all_requests)


@bp.route("/log/<int:request_id>/preview")
@require_login
@csrf.exempt
def log_preview(request_id):
    """Return JSON preview of the sent/forwarded email for a log entry."""
    conn  = get_db()
    role  = session.get("user_role")
    email = session.get("user_email")
    row   = conn.execute(
        "SELECT draft_id, assigned_to FROM requests WHERE id = ?", (request_id,)
    ).fetchone()
    conn.close()

    if not row or not row["draft_id"]:
        return jsonify({"error": "No message ID recorded for this request."})
    if role == "cr_member" and row["assigned_to"] != email:
        return jsonify({"error": "Not authorised to view this message."}), 403

    from modules.email_handler import get_sent_message_preview
    preview = get_sent_message_preview(row["draft_id"])
    return jsonify(preview)


# ── Review Queue ───────────────────────────────────────────────────────────────

@bp.route("/review")
@require_login
def review():
    """Forwarded request queue, scoped by role."""
    conn  = get_db()
    role  = session.get("user_role")
    email = session.get("user_email")

    base_where = "r.status IN ('forwarded', 'flagged', 'pending_clarification')"

    if role == "cr_member":
        flagged = conn.execute(
            f"SELECT r.*, f.filename as matched_filename, f.file_path as matched_file_path "
            f"FROM requests r LEFT JOIN files f ON r.matched_file_id = f.id "
            f"WHERE {base_where} AND r.assigned_to = ? "
            f"ORDER BY r.received_at DESC",
            (email,)
        ).fetchall()
        assignees = []
        assignee  = email
    else:
        assignee = request.args.get("assignee", "")
        if assignee:
            flagged = conn.execute(
                f"SELECT r.*, f.filename as matched_filename, f.file_path as matched_file_path "
                f"FROM requests r LEFT JOIN files f ON r.matched_file_id = f.id "
                f"WHERE {base_where} AND r.assigned_to = ? "
                f"ORDER BY r.received_at DESC",
                (assignee,)
            ).fetchall()
        else:
            flagged = conn.execute(
                f"SELECT r.*, f.filename as matched_filename, f.file_path as matched_file_path "
                f"FROM requests r LEFT JOIN files f ON r.matched_file_id = f.id "
                f"WHERE {base_where} ORDER BY r.received_at DESC"
            ).fetchall()
        assignees_rows = conn.execute(
            "SELECT DISTINCT assigned_to FROM requests "
            "WHERE assigned_to IS NOT NULL AND assigned_to != '' "
            "AND status IN ('forwarded', 'flagged', 'pending_clarification') "
            "ORDER BY assigned_to"
        ).fetchall()
        assignees = [r[0] for r in assignees_rows]

    conn.close()
    return render_template("review.html", flagged=flagged, assignee=assignee, assignees=assignees)


@bp.route("/review/<int:request_id>/handled", methods=["POST"])
@require_login
def mark_handled(request_id):
    """Mark a request as reviewed. CR members can only mark their own."""
    notes = request.form.get("notes", "").strip()
    conn  = get_db()

    if session.get("user_role") == "cr_member":
        row = conn.execute(
            "SELECT assigned_to FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row or row["assigned_to"] != session.get("user_email"):
            conn.close()
            flash("You can only mark your own assigned requests as handled.", "error")
            return redirect(url_for("dashboard.review"))

    conn.execute(
        "UPDATE requests SET status = 'reviewed', handled_at = ?, notes = ? WHERE id = ?",
        (datetime.datetime.now().isoformat(), notes, request_id),
    )
    conn.commit()
    conn.close()
    flash("Request marked as handled.", "success")
    return redirect(url_for("dashboard.review"))


@bp.route("/review/<int:request_id>/reprocess", methods=["POST"])
@require_admin
def reprocess_request(request_id):
    """Re-run file matching for a forwarded or flagged request."""
    conn = get_db()
    row  = conn.execute("SELECT status FROM requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    if not row or row["status"] not in ("forwarded", "flagged"):
        flash("Re-process is only available for forwarded or flagged requests.", "error")
        return redirect(url_for("dashboard.review"))

    from modules.request_processor import reprocess_request as do_reprocess
    result = do_reprocess(request_id)
    flash(result, "success" if "auto_sent" in result or "succeeded" in result else "info")

    if "auto_sent" in result or "succeeded" in result:
        return redirect(url_for("dashboard.log"))
    return redirect(url_for("dashboard.review"))


# ── Strategy Browser ───────────────────────────────────────────────────────────

@bp.route("/strategies")
@require_login
def strategies():
    """Permanent strategy browser — hierarchical view of all strategies."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT s.firm_name, s.investment_style, s.asset_class, s.region,
               s.fund_name, s.vehicle, s.share_class,
               COUNT(f.id) as file_count,
               GROUP_CONCAT(DISTINCT f.access_level) as access_levels
        FROM strategies s
        LEFT JOIN files f ON (
            f.firm_name        = s.firm_name        AND
            f.investment_style = s.investment_style AND
            f.asset_class      = s.asset_class      AND
            f.region           = s.region           AND
            f.fund_name        = s.fund_name        AND
            f.vehicle          = s.vehicle          AND
            f.share_class      = s.share_class
        )
        GROUP BY s.id
        ORDER BY s.firm_name, s.investment_style, s.asset_class,
                 s.region, s.fund_name, s.vehicle
        """
    ).fetchall()
    conn.close()

    tree = {}
    for row in rows:
        row = dict(row)
        f  = row["firm_name"]
        st = row["investment_style"] or "Not Applicable"
        ac = row["asset_class"]
        rg = row["region"]
        fn = row["fund_name"]
        levels = row["access_levels"].split(",") if row["access_levels"] else []

        tree.setdefault(f, {}) \
            .setdefault(st, {}) \
            .setdefault(ac, {}) \
            .setdefault(rg, {}) \
            .setdefault(fn, []) \
            .append({
                "vehicle":       row["vehicle"],
                "share_class":   row["share_class"],
                "file_count":    row["file_count"],
                "access_levels": levels,
            })

    from modules.file_manager import get_stale_files
    stale_rows = get_stale_files()
    stale_fund_keys = set()
    for sf in stale_rows:
        stale_fund_keys.add(f"{sf['firm_name']}|{sf['fund_name']}")

    return render_template("strategies.html", tree=tree, stale_fund_keys=stale_fund_keys)


@bp.route("/strategies/details")
@require_login
def strategy_details():
    """Returns JSON with file descriptions and permissions for a given fund."""
    firm    = request.args.get("firm", "")
    fund    = request.args.get("fund", "")
    vehicle = request.args.get("vehicle", "")

    conn = get_db()

    if vehicle:
        files = conn.execute(
            "SELECT filename, description, access_level, data_type, time_period "
            "FROM files WHERE firm_name=? AND fund_name=? AND vehicle=? ORDER BY upload_date DESC",
            (firm, fund, vehicle),
        ).fetchall()
    else:
        files = conn.execute(
            "SELECT filename, description, access_level, data_type, time_period "
            "FROM files WHERE firm_name=? AND fund_name=? ORDER BY upload_date DESC",
            (firm, fund),
        ).fetchall()

    perms = conn.execute(
        "SELECT id, email_address, vehicle, share_class, granted_by "
        "FROM permissions "
        "WHERE fund_name=? AND (firm_name=? OR firm_name IS NULL OR firm_name='') "
        "ORDER BY email_address",
        (fund, firm),
    ).fetchall()

    conn.close()

    return jsonify({
        "files":       [dict(f) for f in files],
        "permissions": [dict(p) for p in perms],
    })


@bp.route("/strategies/permissions/add", methods=["POST"])
@require_admin
@csrf.exempt
def strategies_add_permission():
    """AJAX: add a permission from the Strategy Browser info modal."""
    data       = request.get_json() or {}
    email      = (data.get("email") or "").strip()
    firm       = (data.get("firm") or "").strip()
    fund       = (data.get("fund") or "").strip()
    vehicle    = (data.get("vehicle") or "").strip()
    share_cls  = (data.get("share_class") or "").strip()
    granted_by = (data.get("granted_by") or "").strip()

    if not email or not fund:
        return jsonify({"error": "Email and fund are required."}), 400
    if not granted_by:
        return jsonify({"error": "Your email is required to record ownership of this permission."}), 400

    result = permissions.add_permission(email, firm, fund, vehicle, share_cls, granted_by)
    return jsonify({"success": True, "permission": result})


@bp.route("/strategies/permissions/<int:perm_id>/remove", methods=["POST"])
@require_admin
@csrf.exempt
def strategies_remove_permission(perm_id):
    """AJAX: remove a permission from the Strategy Browser info modal."""
    data          = request.get_json() or {}
    confirm_email = (data.get("confirm_email") or "").strip()
    success, reason = permissions.remove_permission(perm_id, confirm_email)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": reason}), 403


# ── Configuration Page ─────────────────────────────────────────────────────────

@bp.route("/config")
@require_admin
def config_page():
    """CR routing configuration page."""
    from modules import cr_routing
    regions         = cr_routing.get_all_regions()
    assignments     = cr_routing.get_all_assignments()
    sender_profiles = cr_routing.get_all_sender_profiles()
    load_counts     = {
        r["region_name"]: cr_routing.get_member_load_counts(r["region_name"])
        for r in regions
    }
    return render_template("config.html", regions=regions, assignments=assignments,
                           sender_profiles=sender_profiles, load_counts=load_counts)


@bp.route("/config/regions/add", methods=["POST"])
@require_admin
def config_add_region():
    region_name = request.form.get("region_name", "").strip()
    if not region_name:
        flash("Region name is required.", "error")
        return redirect(url_for("dashboard.config_page"))
    from modules import cr_routing
    success = cr_routing.add_region(region_name)
    if success:
        flash(f"Region '{region_name}' added.", "success")
    else:
        flash(f"Region '{region_name}' already exists.", "error")
    return redirect(url_for("dashboard.config_page"))


@bp.route("/config/regions/<name>/remove", methods=["POST"])
@require_admin
def config_remove_region(name):
    from modules import cr_routing
    cr_routing.remove_region(name)
    flash(f"Region '{name}' removed.", "success")
    return redirect(url_for("dashboard.config_page"))


@bp.route("/config/assignments/add", methods=["POST"])
@require_admin
def config_add_assignment():
    region_name  = request.form.get("region_name", "").strip()
    member_name  = request.form.get("member_name", "").strip()
    member_email = request.form.get("member_email", "").strip()
    if not region_name or not member_name or not member_email:
        flash("Region, member name, and member email are all required.", "error")
        return redirect(url_for("dashboard.config_page"))
    from modules import cr_routing
    cr_routing.add_assignment(region_name, member_name, member_email)
    flash(f"Added {member_name} ({member_email}) for region '{region_name}'.", "success")
    return redirect(url_for("dashboard.config_page"))


@bp.route("/config/assignments/<int:assignment_id>/remove", methods=["POST"])
@require_admin
def config_remove_assignment(assignment_id):
    from modules import cr_routing
    deleted = cr_routing.remove_assignment(assignment_id)
    if deleted:
        flash("Assignment removed.", "success")
    else:
        flash("Assignment not found.", "error")
    return redirect(url_for("dashboard.config_page"))


# ── User Management ────────────────────────────────────────────────────────────

@bp.route("/admin/users")
@require_admin
def user_management():
    """Account management page — admin only."""
    conn  = get_db()
    users = conn.execute(
        "SELECT id, email, name, role, is_active, created_date, last_login, must_change_password "
        "FROM users ORDER BY role DESC, name"
    ).fetchall()
    conn.close()
    return render_template("users.html", users=users)


@bp.route("/admin/users/create", methods=["POST"])
@require_admin
def create_user():
    """Create a new user account."""
    email    = request.form.get("email", "").strip().lower()
    name     = request.form.get("name", "").strip()
    role     = request.form.get("role", "cr_member").strip()
    password = request.form.get("password", "").strip()

    if not email or not name or not password:
        flash("Email, name, and password are all required.", "error")
        return redirect(url_for("dashboard.user_management"))
    if role not in ("admin", "cr_member"):
        flash("Invalid role.", "error")
        return redirect(url_for("dashboard.user_management"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("dashboard.user_management"))

    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        flash(f"A user with email {email} already exists.", "error")
        return redirect(url_for("dashboard.user_management"))

    conn.execute(
        "INSERT INTO users (email, name, password_hash, role, is_active, must_change_password, created_date) "
        "VALUES (?, ?, ?, ?, 1, 1, ?)",
        (email, name, generate_password_hash(password), role, datetime.datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    flash(f"Account created for {name} ({email}) as {role}. They will be prompted to change their password on first login.", "success")
    return redirect(url_for("dashboard.user_management"))


@bp.route("/admin/users/<int:user_id>/deactivate", methods=["POST"])
@require_admin
def deactivate_user(user_id):
    """Soft-delete a user. Cannot deactivate yourself."""
    if session.get("user_id") == user_id:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("dashboard.user_management"))
    conn = get_db()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("User deactivated.", "success")
    return redirect(url_for("dashboard.user_management"))


@bp.route("/admin/users/<int:user_id>/reactivate", methods=["POST"])
@require_admin
def reactivate_user(user_id):
    """Reactivate a deactivated user."""
    conn = get_db()
    conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("User reactivated.", "success")
    return redirect(url_for("dashboard.user_management"))


@bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@require_admin
def reset_user_password(user_id):
    """Admin sets a new password for any user (no old password required)."""
    new_password = request.form.get("new_password", "").strip()
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("dashboard.user_management"))
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    conn.close()
    flash("Password reset. The user will be prompted to change it on next login.", "success")
    return redirect(url_for("dashboard.user_management"))


# ── Self-Service Password Change ───────────────────────────────────────────────

@bp.route("/account/change-password", methods=["GET", "POST"])
@require_login
def change_own_password():
    """Any logged-in user can change their own password."""
    error = None
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_pw  = request.form.get("new_password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()

        if new_pw != confirm:
            error = "New passwords do not match."
        elif len(new_pw) < 8:
            error = "Password must be at least 8 characters."
        else:
            conn = get_db()
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (session["user_id"],)
            ).fetchone()
            if not check_password_hash(user["password_hash"], current):
                error = "Current password is incorrect."
                conn.close()
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                    (generate_password_hash(new_pw), session["user_id"])
                )
                conn.commit()
                conn.close()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard.index"))

    return render_template("change_password.html", error=error)
