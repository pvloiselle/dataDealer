"""
request_processor.py — The core workflow engine
─────────────────────────────────────────────────
This module orchestrates the full DataDealer pipeline for each incoming email:
  1. Log the request to the database
  2. Parse it with Claude AI
  3. Check permissions
  4. Search for a matching file (if approved)
  5. Create a Gmail draft reply (if a match is found)
  6. Update the request record with the outcome

This is the "brain" that connects all the other modules together.
"""

import datetime
from modules.database import get_db
from modules import email_handler, ai_parser, file_manager, permissions


def process_email(msg: dict) -> None:
    """
    Run the full DataDealer pipeline for a single incoming email.

    Args:
        msg: A dict from email_handler.get_message_details(), containing:
             id, thread_id, sender, subject, body, snippet
    """
    sender = msg.get("sender", "")
    subject = msg.get("subject", "")
    body = msg.get("body", "")
    thread_id = msg.get("thread_id", "")
    message_id = msg.get("id", "")

    print(f"\n[Processor] ── Processing email from: {sender}")
    print(f"[Processor]    Subject: {subject}")

    # ── Step 1: Log the incoming request to the database ─────────────────────
    # We log it immediately so it shows up in the dashboard even if later steps fail.
    request_id = _create_request_record(sender, subject, body)

    # ── Step 2: Parse the email with Claude AI ────────────────────────────────
    parsed = ai_parser.parse_email_request(subject, body)

    if not parsed:
        # Claude couldn't parse the email — flag it for human review
        print("[Processor] Parsing failed — flagging for review.")
        _update_request(
            request_id,
            status="flagged",
            flag_reason="AI parsing failed — could not extract request details from this email.",
        )
        return

    # Update the request record with what Claude extracted
    _update_parsed_fields(request_id, parsed)

    fund_name = parsed.get("fund_name")
    vehicle_name = parsed.get("vehicle_name")
    parse_summary = parsed.get("summary", "data request")

    # ── Step 3: Check permissions ─────────────────────────────────────────────
    # Extract just the email address from "Name <email>" format
    sender_email = _extract_email_address(sender)

    approved = permissions.is_approved(sender_email, fund_name, vehicle_name)

    if not approved:
        print(f"[Processor] Sender not approved for fund '{fund_name}' — flagging.")
        _update_request(
            request_id,
            status="flagged",
            flag_reason=(
                f"Sender '{sender_email}' is not on the approved list for "
                f"fund: '{fund_name or 'unknown'}' / "
                f"vehicle: '{vehicle_name or 'any'}'. "
                f"Manual review required before sending any data."
            ),
        )
        return

    # ── Step 4: Search for a matching file ────────────────────────────────────
    # Build a text query from the parsed request and search by semantic similarity
    query = ai_parser.build_search_query(parsed)
    matched_file, score = file_manager.search_files(query)

    if not matched_file:
        print(f"[Processor] No matching file found (best score: {score:.3f}) — flagging.")
        _update_request(
            request_id,
            status="flagged",
            flag_reason=(
                f"Sender is approved, but no matching file was found in the database "
                f"(best similarity score: {score:.2f}, threshold: {__import__('config').SIMILARITY_THRESHOLD}). "
                f"Please upload the relevant file and fulfill this request manually."
            ),
        )
        return

    # ── Step 5: Create a Gmail draft reply ────────────────────────────────────
    # ⚠️  Creates a DRAFT only — human must open Gmail and click Send.
    print(f"[Processor] Match found: '{matched_file['filename']}' (score: {score:.3f})")

    draft_id = email_handler.create_draft_reply(
        thread_id=thread_id,
        to_email=sender_email,
        original_subject=subject,
        attachment_path=matched_file["file_path"],
        parsed_summary=parse_summary,
    )

    # ── Step 6: Update the request record with the outcome ────────────────────
    if draft_id:
        _update_request(
            request_id,
            status="auto_fulfilled",
            matched_file_id=matched_file["id"],
            draft_id=draft_id,
        )
        print(f"[Processor] ✓ Draft created. Request auto-fulfilled (pending human send).")
    else:
        # Draft creation failed (e.g. Gmail API error) — flag for manual handling
        _update_request(
            request_id,
            status="flagged",
            matched_file_id=matched_file["id"],
            flag_reason="File matched successfully, but Gmail draft creation failed. "
                        "Please send the response manually.",
        )
        print("[Processor] Draft creation failed — flagged for manual handling.")


def poll_and_process_inbox() -> int:
    """
    Check the Gmail inbox for new unread messages and process each one.
    This is the function called by the APScheduler every 5 minutes.

    Returns:
        The number of emails processed in this poll cycle.
    """
    print(f"\n[Scheduler] ── Polling inbox at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        unread = email_handler.get_unread_messages()
    except FileNotFoundError as e:
        print(f"[Scheduler] Gmail not configured yet: {e}")
        return 0
    except Exception as e:
        print(f"[Scheduler] Error connecting to Gmail: {e}")
        return 0

    if not unread:
        print("[Scheduler] No new emails.")
        return 0

    processed = 0
    for msg_stub in unread:
        msg_id = msg_stub["id"]

        # Mark as read FIRST — if processing fails partway, we don't want
        # to re-process the same email on the next poll cycle.
        email_handler.mark_as_read(msg_id)

        # Fetch full message details
        msg = email_handler.get_message_details(msg_id)
        if not msg:
            continue

        # Run the full pipeline
        try:
            process_email(msg)
            processed += 1
        except Exception as e:
            print(f"[Processor] Unexpected error processing email {msg_id}: {e}")
            # Don't crash the whole scheduler — continue to next email

    print(f"[Scheduler] Processed {processed} email(s) this cycle.")
    return processed


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_email_address(sender: str) -> str:
    """
    Extract the bare email address from a header like 'Name <email@domain.com>'
    or just 'email@domain.com'.
    """
    sender = sender.strip()
    if "<" in sender and ">" in sender:
        return sender.split("<")[1].split(">")[0].strip().lower()
    return sender.lower()


def _create_request_record(sender: str, subject: str, body: str) -> int:
    """Insert a new request record and return its database ID."""
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO requests (sender_email, subject, body, received_at, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (sender, subject, body, datetime.datetime.now().isoformat()),
    )
    conn.commit()
    request_id = cursor.lastrowid
    conn.close()
    return request_id


def _update_parsed_fields(request_id: int, parsed: dict) -> None:
    """Update a request record with the fields Claude extracted."""
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET
            parsed_fund = ?,
            parsed_vehicle = ?,
            parsed_data_type = ?,
            parsed_period = ?,
            parse_confidence = ?,
            parse_summary = ?
        WHERE id = ?
        """,
        (
            parsed.get("fund_name"),
            parsed.get("vehicle_name"),
            parsed.get("data_type"),
            parsed.get("time_period"),
            parsed.get("confidence"),
            parsed.get("summary"),
            request_id,
        ),
    )
    conn.commit()
    conn.close()


def _update_request(request_id: int, status: str, matched_file_id: int = None,
                    draft_id: str = None, flag_reason: str = None) -> None:
    """Update a request record with its final outcome."""
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET
            status = ?,
            matched_file_id = ?,
            draft_id = ?,
            flag_reason = ?,
            handled_at = CASE WHEN ? = 'auto_fulfilled' THEN ? ELSE NULL END
        WHERE id = ?
        """,
        (
            status,
            matched_file_id,
            draft_id,
            flag_reason,
            status,
            datetime.datetime.now().isoformat(),
            request_id,
        ),
    )
    conn.commit()
    conn.close()
