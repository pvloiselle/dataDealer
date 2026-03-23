"""
request_processor.py — The core workflow engine
─────────────────────────────────────────────────
Orchestrates the full DataDealer pipeline for each incoming email.

Outcome logic:
  AUTO-SEND      → sender approved + file score ≥ HIGH_CONFIDENCE_THRESHOLD
                   + Claude parse confidence = "high"
  FORWARD        → everything else (unapproved sender, borderline score,
                   low parse confidence, parse failure, no file match)
                   → email sent to CONSULTANT_EMAIL with full context

The dashboard review queue still logs all forwarded/auto-sent requests,
but uncertain cases are now actively pushed to a human via email.
"""

import datetime
import config
from modules.database import get_db
from modules import email_handler, ai_parser, file_manager, permissions


def process_email(msg: dict) -> None:
    """
    Run the full DataDealer pipeline for a single incoming email.

    Args:
        msg: dict from email_handler.get_message_details()
             keys: id, thread_id, sender, subject, body, snippet
    """
    sender = msg.get("sender", "")
    subject = msg.get("subject", "")
    body = msg.get("body", "")
    thread_id = msg.get("thread_id", "")

    print(f"\n[Processor] ── Processing email from: {sender}")
    print(f"[Processor]    Subject: {subject}")

    # ── Step 1: Log the request immediately ──────────────────────────────────
    request_id = _create_request_record(sender, subject, body)

    sender_email = _extract_email_address(sender)

    # ── Step 1b: CR region routing (if configured) ────────────────────────────
    from modules import cr_routing as cr_mod
    assigned_to = None
    if cr_mod.cr_routing_enabled():
        region = cr_mod.get_sender_region(sender_email)
        if region is None:
            # Unknown region — send clarification and halt
            print(f"[Processor] Sender region unknown — sending clarification to {sender_email}.")
            region_list = [r["region_name"] for r in cr_mod.get_all_regions()]
            sent_id = email_handler.send_clarification_email(
                thread_id=thread_id,
                to_email=sender_email,
                original_subject=subject,
                region_list=region_list,
            )
            conn = get_db()
            conn.execute(
                "UPDATE requests SET status='pending_clarification', clarification_thread_id=?, draft_id=? WHERE id=?",
                (thread_id, sent_id, request_id)
            )
            conn.commit()
            conn.close()
            print(f"[Processor] Request {request_id} set to pending_clarification.")
            _notify_queue(
                to_email=assigned_to or config.NOTIFICATION_EMAIL,
                sender_email=sender_email,
                subject=subject,
                parse_summary=None,
                status="pending_clarification",
                request_id=request_id,
            )
            return
        else:
            member = cr_mod.get_least_loaded_member(region)
            assigned_to = member["member_email"] if member else None
            load = member["outstanding_count"] if member else "N/A"
            print(f"[Processor] Sender region: '{region}', assigned to: {assigned_to} (outstanding: {load})")

    # ── Step 2: Parse with Claude ─────────────────────────────────────────────
    parsed = ai_parser.parse_email_request(subject, body)

    if not parsed:
        print("[Processor] Parsing failed — forwarding to consultant.")
        sent_id = email_handler.forward_to_consultant(
            original_msg=msg,
            parsed=None,
            matched_file=None,
            match_score=0.0,
            reason="The AI was unable to extract structured data from this email. "
                   "It may be malformed, off-topic, or written in an unexpected format. "
                   "Please review the original email and respond manually if appropriate.",
            to_email=assigned_to or config.CONSULTANT_EMAIL,
        )
        _update_request(request_id, status="forwarded",
                        flag_reason="AI parsing failed.", forwarded_id=sent_id)
        return

    _update_parsed_fields(request_id, parsed)

    firm_name    = parsed.get("firm_name")
    fund_name    = parsed.get("fund_name")
    vehicle      = parsed.get("vehicle")
    share_class  = parsed.get("share_class")
    parse_conf   = parsed.get("confidence", "low")
    parse_summary = parsed.get("summary", "data request")

    # ── Step 3: Search for a matching file ───────────────────────────────────
    # (Search first so we know the file's access_level before checking permissions)
    query = ai_parser.build_search_query(parsed)
    matched_file, score = file_manager.search_files(query)

    # ── Step 4: Check permissions (only required for restricted files) ────────
    file_is_public = (
        matched_file is not None
        and matched_file.get("access_level") == "public"
    )

    if file_is_public:
        # Public files skip the permission check entirely
        approved = True
        print(f"[Processor] File is public — permission check skipped.")
    else:
        approved = permissions.is_approved(sender_email, fund_name, firm_name, vehicle, share_class)

    # ── Step 5: Determine confidence and route accordingly ───────────────────
    is_confident = (
        approved
        and matched_file is not None
        and score >= config.HIGH_CONFIDENCE_THRESHOLD
        and parse_conf == "high"
    )

    if is_confident:
        # ── HIGH CONFIDENCE: send automatically ──────────────────────────────
        print(f"[Processor] High confidence (score: {score:.3f}, parse: {parse_conf}) — auto-sending.")
        sent_id = email_handler.send_auto_response(
            thread_id=thread_id,
            to_email=sender_email,
            original_subject=subject,
            attachment_path=matched_file["file_path"],
            parsed_summary=parse_summary,
            upload_date=(matched_file.get("upload_date") or "")[:10],
        )
        if sent_id:
            _update_request(request_id, status="auto_sent",
                            matched_file_id=matched_file["id"], sent_id=sent_id)
            print(f"[Processor] ✓ Auto-response sent.")
        else:
            # Send failed — fall back to forwarding
            print("[Processor] Auto-send failed — falling back to forwarding consultant.")
            _forward_uncertain(
                request_id, msg, parsed, matched_file, score,
                reason="Auto-send failed due to a Gmail API error. "
                       "The sender is approved and a file was matched — please send manually.",
                assigned_to=assigned_to,
            )
    else:
        # ── UNCERTAIN: forward to consultant with context ─────────────────────
        reason = _build_uncertainty_reason(
            approved, parsed, matched_file, score, parse_conf, fund_name, vehicle, share_class
        )
        print(f"[Processor] Uncertain — forwarding to consultant. Reason: {reason}")
        _forward_uncertain(request_id, msg, parsed, matched_file, score, reason,
                           assigned_to=assigned_to)


def _forward_uncertain(request_id, msg, parsed, matched_file, score, reason, assigned_to=None):
    """Forward an uncertain request to the consultant and update the DB record."""
    to_email = assigned_to or config.CONSULTANT_EMAIL
    sent_id = email_handler.forward_to_consultant(
        original_msg=msg,
        parsed=parsed,
        matched_file=matched_file,
        match_score=score,
        reason=reason,
        to_email=to_email,
    )
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET
            status=?, matched_file_id=?, draft_id=?, flag_reason=?, handled_at=?, assigned_to=?
        WHERE id=?
        """,
        (
            "forwarded",
            matched_file["id"] if matched_file else None,
            sent_id,
            reason,
            None,
            assigned_to,
            request_id,
        ),
    )
    conn.commit()
    conn.close()


def _build_uncertainty_reason(approved, parsed, matched_file, score,
                               parse_conf, fund_name, vehicle, share_class) -> str:
    """
    Build a plain-English explanation of why a request could not be auto-fulfilled.
    This is included in the forwarding email to give the consultant full context.
    """
    reasons = []

    if not approved and not (matched_file and matched_file.get("access_level") == "public"):
        parts = [f"'{parsed.get('fund_name', 'unknown fund')}'"]
        if vehicle:
            parts.append(f"vehicle '{vehicle}'")
        if share_class:
            parts.append(f"share class '{share_class}'")
        reasons.append(
            f"The sender is not on the approved list for {' / '.join(parts)}. "
            f"This document is marked as restricted."
        )

    if matched_file is None:
        reasons.append(
            f"No file in the database matched this request above the similarity threshold "
            f"(threshold: {config.SIMILARITY_THRESHOLD}). The requested data may not have been uploaded yet."
        )
    elif score < config.HIGH_CONFIDENCE_THRESHOLD:
        reasons.append(
            f"A potential file match was found ('{matched_file.get('filename', '')}', "
            f"score: {score:.2f}) but it fell below the high-confidence threshold "
            f"({config.HIGH_CONFIDENCE_THRESHOLD})."
        )
        # Explicitly flag if the requested fund name differs from the matched file's fund name.
        # Normalize first: strip the firm name prefix from the requested fund name, since
        # consultants often say "Vanguard Growth ETF" when the file is tagged firm="Vanguard",
        # fund="Growth ETF". These are the same thing and should not trigger a mismatch warning.
        requested_fund = (parsed.get("fund_name") or "").strip().lower()
        matched_fund = (matched_file.get("fund_name") or "").strip().lower()
        firm = (matched_file.get("firm_name") or parsed.get("firm_name") or "").strip().lower()
        if firm and requested_fund.startswith(firm):
            requested_fund_normalized = requested_fund[len(firm):].strip()
        else:
            requested_fund_normalized = requested_fund
        if requested_fund and matched_fund and requested_fund_normalized != matched_fund:
            reasons.append(
                f"⚠️  Fund name mismatch: the request is for '{parsed.get('fund_name')}' "
                f"but the best-matching file is tagged as '{matched_file.get('fund_name')}'. "
                f"These may be different strategies — do not send without verifying."
            )

    if parse_conf in ("medium", "low"):
        reasons.append(
            f"The AI extracted the request with {parse_conf} confidence — "
            f"some details may have been inferred rather than stated explicitly. "
            f"Please verify the request summary against the original email."
        )

    # Stale file warning
    if matched_file is not None:
        from modules.file_manager import is_stale
        if is_stale(matched_file):
            next_upd = matched_file.get("next_update_date", "")
            upload_d = (matched_file.get("upload_date") or "")[:10]
            reasons.append(
                f"⚠️  The matched file may be outdated — it was uploaded on {upload_d} "
                f"and its expected next update was {next_upd}. "
                f"Please verify whether a more recent version is available before sending."
            )

    return " ".join(reasons) if reasons else "Request did not meet auto-fulfillment criteria."


def poll_and_process_inbox() -> int:
    """
    Check Gmail for new unread messages and process each one.
    Called by APScheduler every N minutes.
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

        # Mark as read first — prevents reprocessing if something errors mid-pipeline
        email_handler.mark_as_read(msg_id)

        msg = email_handler.get_message_details(msg_id)
        if not msg:
            continue

        try:
            original_req = _find_pending_clarification_by_thread(msg.get("thread_id", ""))
            if original_req:
                handle_clarification_reply(msg, original_req)
            else:
                process_email(msg)
            processed += 1
        except Exception as e:
            print(f"[Processor] Unexpected error processing email {msg_id}: {e}")
            # Log a failed record so it shows up in the dashboard
            _log_hard_failure(msg, str(e))

    print(f"[Scheduler] Processed {processed} email(s) this cycle.")
    return processed


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_email_address(sender: str) -> str:
    sender = sender.strip()
    if "<" in sender and ">" in sender:
        return sender.split("<")[1].split(">")[0].strip().lower()
    return sender.lower()


def _create_request_record(sender, subject, body) -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO requests (sender_email, subject, body, received_at, status) VALUES (?, ?, ?, ?, 'pending')",
        (sender, subject, body, datetime.datetime.now().isoformat()),
    )
    conn.commit()
    request_id = cursor.lastrowid
    conn.close()
    return request_id


def _update_parsed_fields(request_id, parsed):
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET
            parsed_fund=?, parsed_vehicle=?, parsed_data_type=?, parsed_period=?,
            parse_confidence=?, parse_summary=?
        WHERE id=?
        """,
        (
            parsed.get("fund_name"), parsed.get("vehicle"), parsed.get("data_type"),
            parsed.get("time_period"), parsed.get("confidence"), parsed.get("summary"),
            request_id,
        ),
    )
    conn.commit()
    conn.close()


def _update_request(request_id, status, matched_file_id=None,
                    sent_id=None, forwarded_id=None, flag_reason=None):
    conn = get_db()
    conn.execute(
        """
        UPDATE requests SET
            status=?, matched_file_id=?, draft_id=?, flag_reason=?, handled_at=?
        WHERE id=?
        """,
        (
            status,
            matched_file_id,
            sent_id or forwarded_id,   # reuse draft_id column for sent/forwarded message IDs
            flag_reason,
            datetime.datetime.now().isoformat() if status == "auto_sent" else None,
            request_id,
        ),
    )
    conn.commit()
    conn.close()


def _log_hard_failure(msg, error_message):
    """Log a completely unexpected error as a forwarded record so nothing is silently lost."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO requests (sender_email, subject, body, received_at, status, flag_reason)
        VALUES (?, ?, ?, ?, 'forwarded', ?)
        """,
        (
            msg.get("sender", "unknown"),
            msg.get("subject", ""),
            msg.get("body", ""),
            datetime.datetime.now().isoformat(),
            f"Unexpected processing error: {error_message}",
        ),
    )
    conn.commit()
    conn.close()


def _find_pending_clarification_by_thread(thread_id: str):
    if not thread_id:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM requests WHERE status='pending_clarification' AND clarification_thread_id=?",
        (thread_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _extract_region_from_reply(body: str, valid_regions: list) -> str:
    """Use Claude to extract a region name from a clarification reply."""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        regions_list = "\n".join(f"- {r}" for r in valid_regions)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=64,
            messages=[{
                "role": "user",
                "content": (
                    f"An investment consultant replied to a clarification email asking which region they are based in.\n"
                    f"The valid regions are:\n{regions_list}\n\n"
                    f"Their reply:\n{body[:500]}\n\n"
                    f"Which region did they mention? Reply with ONLY the exact region name from the list above, "
                    f"or the word UNKNOWN if it cannot be determined."
                )
            }]
        )
        result = response.content[0].text.strip()
        for r in valid_regions:
            if r.lower() == result.lower():
                return r
        return None
    except Exception as e:
        print(f"[Processor] Error extracting region: {e}")
        return None


def handle_clarification_reply(msg: dict, original_request: dict) -> None:
    """
    Process an email that is a reply to our region clarification.
    Extracts the region, saves it to sender_profiles, and re-routes the original request.
    """
    from modules import cr_routing, file_manager
    from modules.ai_parser import build_search_query

    sender_email = _extract_email_address(msg.get("sender", ""))
    body = msg.get("body", "")

    print(f"[Processor] Handling clarification reply from {sender_email}")

    regions = [r["region_name"] for r in cr_routing.get_all_regions()]
    region = _extract_region_from_reply(body, regions)

    if not region:
        print(f"[Processor] Could not extract region from reply — forwarding to consultant.")
        original_msg = {
            "sender": original_request["sender_email"],
            "subject": original_request["subject"],
            "body": original_request["body"],
            "thread_id": original_request.get("clarification_thread_id", ""),
        }
        sent_id = email_handler.forward_to_consultant(
            original_msg=original_msg, parsed=None, matched_file=None, match_score=0.0,
            reason="Region clarification email was sent but a valid region could not be extracted from the reply. Please follow up manually."
        )
        _update_request(original_request["id"], status="forwarded",
                        flag_reason="Could not extract region from clarification reply.", forwarded_id=sent_id)
        return

    cr_routing.set_sender_region(sender_email, region)
    member = cr_routing.get_least_loaded_member(region)
    assigned_to = member["member_email"] if member else None
    load = member["outstanding_count"] if member else "N/A"
    print(f"[Processor] Region '{region}' confirmed for {sender_email}, assigned to: {assigned_to} (outstanding: {load})")

    # Reconstruct parsed data from stored request fields
    original_parsed = {
        "firm_name": original_request.get("parsed_firm"),
        "fund_name": original_request.get("parsed_fund"),
        "vehicle": original_request.get("parsed_vehicle"),
        "data_type": original_request.get("parsed_data_type"),
        "time_period": original_request.get("parsed_period"),
        "confidence": original_request.get("parse_confidence"),
        "summary": original_request.get("parse_summary"),
    }

    # Re-run file search
    query = build_search_query(original_parsed)
    matched_file, score = file_manager.search_files(query)

    # Check permissions
    file_is_public = matched_file is not None and matched_file.get("access_level") == "public"
    if file_is_public:
        approved = True
    else:
        from modules import permissions
        approved = permissions.is_approved(
            sender_email,
            original_parsed.get("fund_name"),
            original_parsed.get("firm_name"),
            original_parsed.get("vehicle"),
            None,
        )

    reason = f"Region identified as '{region}'. " + _build_uncertainty_reason(
        approved, original_parsed, matched_file, score,
        original_parsed.get("confidence", "low"),
        original_parsed.get("fund_name"),
        original_parsed.get("vehicle"), None
    )

    original_msg = {
        "sender": original_request["sender_email"],
        "subject": original_request["subject"],
        "body": original_request["body"],
        "thread_id": original_request.get("clarification_thread_id", ""),
    }

    sent_id = email_handler.forward_to_consultant(
        original_msg=original_msg,
        parsed=original_parsed,
        matched_file=matched_file,
        match_score=score,
        reason=reason,
        to_email=assigned_to or config.CONSULTANT_EMAIL,
    )

    conn = get_db()
    conn.execute(
        """UPDATE requests SET status='forwarded', assigned_to=?, matched_file_id=?,
           flag_reason=?, draft_id=? WHERE id=?""",
        (assigned_to,
         matched_file["id"] if matched_file else None,
         reason, sent_id,
         original_request["id"])
    )
    conn.commit()
    conn.close()
    print(f"[Processor] Clarification reply processed — original request re-routed to {assigned_to or config.CONSULTANT_EMAIL}.")


def _notify_queue(to_email, sender_email, subject, parse_summary, status, request_id):
    """Wrap send_queue_notification in try/except; silently no-ops if to_email is empty."""
    if not to_email:
        return
    try:
        email_handler.send_queue_notification(
            to_email=to_email,
            sender_email=sender_email,
            subject=subject,
            parse_summary=parse_summary,
            status=status,
            request_id=request_id,
        )
    except Exception as e:
        print(f"[Processor] Queue notification failed (non-fatal): {e}")


def reprocess_request(request_id: int) -> str:
    """
    Re-run file matching and routing for a previously forwarded or flagged request.
    Returns a plain-English result string for use as a flash message.

    Steps:
      1. Fetch stored request row and reconstruct parsed dict
      2. Re-run search (superseded files excluded by Feature 1)
      3. Re-check permissions and confidence thresholds
      4. If auto-send criteria met: send response, update row to auto_sent
      5. Otherwise: update matched_file_id/flag_reason only — do NOT re-send forwarding email
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()

    if not row:
        return f"Request {request_id} not found."

    row = dict(row)

    # Reconstruct parsed from stored columns
    parsed = {
        "firm_name":   row.get("parsed_firm"),
        "fund_name":   row.get("parsed_fund"),
        "vehicle":     row.get("parsed_vehicle"),
        "data_type":   row.get("parsed_data_type"),
        "time_period": row.get("parsed_period"),
        "confidence":  row.get("parse_confidence"),
        "summary":     row.get("parse_summary"),
    }

    from modules import ai_parser
    query = ai_parser.build_search_query(parsed) or parsed.get("summary") or ""
    if not query:
        return "Cannot re-process: no parsed data available to build a search query."

    matched_file, score = file_manager.search_files(query)

    sender_email = _extract_email_address(row.get("sender_email", ""))
    fund_name   = parsed.get("fund_name")
    firm_name   = parsed.get("firm_name")
    vehicle     = parsed.get("vehicle")
    share_class = parsed.get("share_class")
    parse_conf  = parsed.get("confidence", "low")
    parse_summary = parsed.get("summary", "data request")

    file_is_public = matched_file is not None and matched_file.get("access_level") == "public"
    if file_is_public:
        approved = True
    else:
        approved = permissions.is_approved(sender_email, fund_name, firm_name, vehicle, share_class)

    is_confident = (
        approved
        and matched_file is not None
        and score >= config.HIGH_CONFIDENCE_THRESHOLD
        and parse_conf == "high"
    )

    if is_confident:
        # Auto-send the file
        sent_id = email_handler.send_auto_response(
            thread_id=row.get("clarification_thread_id") or "",
            to_email=sender_email,
            original_subject=row.get("subject", ""),
            attachment_path=matched_file["file_path"],
            parsed_summary=parse_summary,
            upload_date=(matched_file.get("upload_date") or "")[:10],
        )
        if sent_id:
            conn = get_db()
            conn.execute(
                """UPDATE requests SET status='auto_sent', matched_file_id=?, draft_id=?,
                   flag_reason=NULL, handled_at=? WHERE id=?""",
                (matched_file["id"], sent_id, datetime.datetime.now().isoformat(), request_id)
            )
            conn.commit()
            conn.close()
            return (f"Re-process succeeded: auto-sent '{matched_file['filename']}' "
                    f"to {sender_email} (score: {score:.2f}).")
        else:
            return "Re-process: file matched but auto-send failed. Check Gmail API."
    else:
        # Update match info but don't re-send a forwarding email
        reason = _build_uncertainty_reason(
            approved, parsed, matched_file, score, parse_conf, fund_name, vehicle, share_class
        )
        conn = get_db()
        conn.execute(
            "UPDATE requests SET matched_file_id=?, flag_reason=? WHERE id=?",
            (matched_file["id"] if matched_file else None, reason, request_id)
        )
        conn.commit()
        conn.close()
        if matched_file:
            return (f"Re-process: best match is now '{matched_file['filename']}' "
                    f"(score: {score:.2f}) but criteria for auto-send not met. "
                    f"Reason: {reason}")
        else:
            return f"Re-process: no file match found above threshold. Reason: {reason}"
