"""
permissions.py — Approved sender management
────────────────────────────────────────────
Controls which email addresses are allowed to receive auto-fulfilled responses
for which funds/vehicles.

⚠️  Core safety rule: if an email address is NOT in this table for the
    requested fund, the request is NEVER auto-fulfilled — it is always
    flagged for human review.
"""

import datetime
from modules.database import get_db


def is_approved(sender_email: str, fund_name: str | None, vehicle_name: str | None = None) -> bool:
    """
    Check whether a sender email is approved for a specific fund/vehicle.

    Matching logic (most specific to least specific):
      1. Exact match on email + fund + vehicle (if vehicle is specified)
      2. Match on email + fund with no vehicle restriction (blanket fund approval)

    This means you can approve "consultant@firm.com" for all vehicles of
    "Flagship Fund" by leaving vehicle_name blank when adding the permission.

    Args:
        sender_email:  The email address of the person who sent the request
        fund_name:     The fund they are requesting data for
        vehicle_name:  The specific vehicle (optional)

    Returns:
        True if approved, False if not.
    """
    if not sender_email or not fund_name:
        return False

    # Normalize: lowercase email for case-insensitive matching
    sender_email = sender_email.lower().strip()

    # Extract just the address from "Name <email@domain.com>" format
    if "<" in sender_email and ">" in sender_email:
        sender_email = sender_email.split("<")[1].split(">")[0].strip()

    conn = get_db()

    # First check: email + fund + vehicle (most specific)
    if vehicle_name:
        row = conn.execute(
            """
            SELECT id FROM permissions
            WHERE LOWER(email_address) = ?
              AND LOWER(fund_name) = ?
              AND (LOWER(vehicle_name) = ? OR vehicle_name = '' OR vehicle_name IS NULL)
            LIMIT 1
            """,
            (sender_email, fund_name.lower(), vehicle_name.lower()),
        ).fetchone()
    else:
        # No vehicle specified — check if email is approved for this fund at all
        row = conn.execute(
            """
            SELECT id FROM permissions
            WHERE LOWER(email_address) = ?
              AND LOWER(fund_name) = ?
            LIMIT 1
            """,
            (sender_email, fund_name.lower()),
        ).fetchone()

    conn.close()
    approved = row is not None
    print(f"[Permissions] {sender_email} for '{fund_name}': {'APPROVED' if approved else 'NOT APPROVED'}")
    return approved


def add_permission(email_address: str, fund_name: str, vehicle_name: str = "") -> dict:
    """
    Add an approved email address for a fund/vehicle.
    If the permission already exists, returns the existing record instead.

    Returns:
        The newly created (or existing) permission record as a dict.
    """
    email_address = email_address.lower().strip()
    conn = get_db()

    # Check if it already exists to avoid duplicates
    existing = conn.execute(
        """
        SELECT * FROM permissions
        WHERE LOWER(email_address) = ? AND LOWER(fund_name) = ?
          AND LOWER(COALESCE(vehicle_name, '')) = ?
        """,
        (email_address, fund_name.lower(), (vehicle_name or "").lower()),
    ).fetchone()

    if existing:
        conn.close()
        return dict(existing)

    # Insert new permission
    cursor = conn.execute(
        """
        INSERT INTO permissions (email_address, fund_name, vehicle_name, added_date)
        VALUES (?, ?, ?, ?)
        """,
        (email_address, fund_name, vehicle_name or "", datetime.datetime.now().isoformat()),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    print(f"[Permissions] Added: {email_address} → {fund_name} / {vehicle_name or 'all vehicles'}")
    return {
        "id": new_id,
        "email_address": email_address,
        "fund_name": fund_name,
        "vehicle_name": vehicle_name or "",
    }


def remove_permission(permission_id: int) -> bool:
    """
    Remove a permission by its database ID.
    Returns True if deleted, False if the ID wasn't found.
    """
    conn = get_db()
    result = conn.execute("DELETE FROM permissions WHERE id = ?", (permission_id,))
    conn.commit()
    deleted = result.rowcount > 0
    conn.close()

    if deleted:
        print(f"[Permissions] Removed permission ID: {permission_id}")
    return deleted


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
