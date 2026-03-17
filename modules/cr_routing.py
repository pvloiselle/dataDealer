"""
cr_routing.py — CR team region configuration and sender routing
"""
import datetime
from modules.database import get_db


def cr_routing_enabled() -> bool:
    """Returns True if at least one CR region has been configured."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM cr_regions").fetchone()[0]
    conn.close()
    return count > 0

def get_all_regions() -> list:
    conn = get_db()
    rows = conn.execute("SELECT * FROM cr_regions ORDER BY region_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_region(region_name: str) -> bool:
    conn = get_db()
    try:
        conn.execute("INSERT INTO cr_regions (region_name, created_date) VALUES (?, ?)",
                     (region_name.strip(), datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False  # duplicate

def remove_region(region_name: str):
    conn = get_db()
    conn.execute("DELETE FROM cr_regions WHERE region_name=?", (region_name,))
    conn.execute("DELETE FROM cr_assignments WHERE region_name=?", (region_name,))
    conn.commit()
    conn.close()

def get_all_assignments() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM cr_assignments ORDER BY region_name, member_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_assignment(region_name: str, member_name: str, member_email: str) -> int:
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO cr_assignments (region_name, member_name, member_email, created_date) VALUES (?,?,?,?)",
            (region_name, member_name.strip(), member_email.lower().strip(),
             datetime.datetime.now().isoformat())
        )
        conn.commit()
        new_id = cursor.lastrowid
    except Exception:
        row = conn.execute(
            "SELECT id FROM cr_assignments WHERE region_name=? AND LOWER(member_email)=?",
            (region_name, member_email.lower().strip())
        ).fetchone()
        new_id = row["id"] if row else None
    conn.close()
    return new_id

def remove_assignment(assignment_id: int) -> bool:
    conn = get_db()
    result = conn.execute("DELETE FROM cr_assignments WHERE id=?", (assignment_id,))
    conn.commit()
    deleted = result.rowcount > 0
    conn.close()
    return deleted

def get_cr_members_for_region(region_name: str) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM cr_assignments WHERE LOWER(region_name)=? ORDER BY member_name",
        (region_name.lower(),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_sender_region(email_address: str):
    conn = get_db()
    row = conn.execute(
        "SELECT region_name FROM sender_profiles WHERE LOWER(email_address)=?",
        (email_address.lower(),)
    ).fetchone()
    conn.close()
    return row["region_name"] if row else None

def set_sender_region(email_address: str, region_name: str):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM sender_profiles WHERE LOWER(email_address)=?",
        (email_address.lower(),)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE sender_profiles SET region_name=? WHERE LOWER(email_address)=?",
            (region_name, email_address.lower())
        )
    else:
        conn.execute(
            "INSERT INTO sender_profiles (email_address, region_name, created_date) VALUES (?,?,?)",
            (email_address.lower(), region_name, datetime.datetime.now().isoformat())
        )
    conn.commit()
    conn.close()

def get_all_sender_profiles() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT sp.*, ca.member_name FROM sender_profiles sp "
        "LEFT JOIN cr_assignments ca ON LOWER(ca.region_name)=LOWER(sp.region_name) "
        "GROUP BY sp.id ORDER BY sp.email_address"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
