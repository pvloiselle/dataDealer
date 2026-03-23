"""
permissions.py — Approved sender management
────────────────────────────────────────────
Controls which email addresses are allowed to receive auto-fulfilled responses
for which funds/vehicles/share classes.

⚠️  Core safety rule: if an email address is NOT in this table for the
    requested fund, the request is NEVER auto-fulfilled — it is always
    flagged for human review.
"""

import datetime
from modules.database import get_db


def is_approved(sender_email: str, fund_name: str | None, firm_name: str | None = None,
                vehicle: str | None = None, share_class: str | None = None) -> bool:
    """
    Check whether a sender email is approved for a specific fund/vehicle/share class.

    Matching logic (most specific to least specific):
      1. email + fund + vehicle + share class
      2. email + fund + vehicle (any share class)
      3. email + fund (any vehicle or share class — blanket fund approval)

    Leaving vehicle and share_class blank when adding a permission grants
    access to all vehicles and share classes within that fund.
    """
    if not sender_email or not fund_name:
        return False

    sender_email = sender_email.lower().strip()
    if "<" in sender_email and ">" in sender_email:
        sender_email = sender_email.split("<")[1].split(">")[0].strip()

    conn = get_db()

    row = conn.execute(
        """
        SELECT id FROM permissions
        WHERE LOWER(email_address) = ?
          AND LOWER(fund_name) = ?
          AND (firm_name    = '' OR firm_name    IS NULL OR LOWER(firm_name)    = LOWER(COALESCE(?, '')))
          AND (vehicle      = '' OR vehicle      IS NULL OR LOWER(vehicle)      = LOWER(COALESCE(?, '')))
          AND (share_class  = '' OR share_class  IS NULL OR LOWER(share_class)  = LOWER(COALESCE(?, '')))
        LIMIT 1
        """,
        (sender_email, fund_name.lower(), firm_name or "", vehicle or "", share_class or ""),
    ).fetchone()

    conn.close()
    approved = row is not None
    print(f"[Permissions] {sender_email} for '{fund_name}': {'APPROVED' if approved else 'NOT APPROVED'}")
    return approved


def add_permission(email_address: str, firm_name: str = "", fund_name: str = "",
                   vehicle: str = "", share_class: str = "",
                   granted_by: str = "") -> dict:
    """
    Add an approved email address for a fund, optionally scoped to a vehicle and share class.
    If the permission already exists, returns the existing record instead.

    granted_by: email address of the person adding this permission (for ownership tracking).
    """
    email_address = email_address.lower().strip()
    granted_by = granted_by.lower().strip()
    conn = get_db()

    existing = conn.execute(
        """
        SELECT * FROM permissions
        WHERE LOWER(email_address) = ? AND LOWER(fund_name) = ?
          AND LOWER(COALESCE(firm_name, '')) = ?
          AND LOWER(COALESCE(vehicle, '')) = ?
          AND LOWER(COALESCE(share_class, '')) = ?
        """,
        (email_address, fund_name.lower(), (firm_name or "").lower(),
         (vehicle or "").lower(), (share_class or "").lower()),
    ).fetchone()

    if existing:
        conn.close()
        return dict(existing)

    cursor = conn.execute(
        """
        INSERT INTO permissions (email_address, firm_name, fund_name, vehicle, share_class, added_date, granted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (email_address, firm_name or "", fund_name, vehicle or "", share_class or "",
         datetime.datetime.now().isoformat(), granted_by),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    label = fund_name
    if vehicle:
        label += f" / {vehicle}"
    if share_class:
        label += f" / {share_class}"
    print(f"[Permissions] Added: {email_address} → {label} (granted by: {granted_by or 'unrecorded'})")
    return {
        "id": new_id,
        "email_address": email_address,
        "fund_name": fund_name,
        "vehicle": vehicle or "",
        "share_class": share_class or "",
        "granted_by": granted_by,
    }


def remove_permission(permission_id: int, requesting_email: str = "") -> tuple[bool, str]:
    """
    Remove a permission by its database ID.

    requesting_email: the email of the person attempting the removal.
    If the permission has a recorded granted_by, this must match (case-insensitive).
    Legacy rows with no granted_by recorded can be removed by anyone.

    Returns (success, message).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT granted_by FROM permissions WHERE id = ?", (permission_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False, "Permission not found."

    stored_granter = (row["granted_by"] or "").lower().strip()
    incoming = (requesting_email or "").lower().strip()

    # If the permission has a recorded granter, the caller must match it.
    # Legacy rows (no granter recorded) can be freely deleted.
    if stored_granter and incoming != stored_granter:
        conn.close()
        return False, (
            f"This permission was granted by {stored_granter}. "
            f"Only they can remove it — enter their email to confirm."
        )

    conn.execute("DELETE FROM permissions WHERE id = ?", (permission_id,))
    conn.commit()
    conn.close()
    print(f"[Permissions] Removed permission ID: {permission_id}")
    return True, "Permission removed."


def get_all_permissions() -> list[dict]:
    """Return all permissions (for the admin dashboard)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM permissions ORDER BY fund_name, email_address"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_funds_for_email(email_address: str) -> list[str]:
    """Return all funds that a given email address is approved for."""
    email_address = email_address.lower().strip()
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT fund_name FROM permissions WHERE LOWER(email_address) = ?",
        (email_address,),
    ).fetchall()
    conn.close()
    return [r["fund_name"] for r in rows]
