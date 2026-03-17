"""
email_handler.py — Gmail API integration
─────────────────────────────────────────
Handles:
  1. Authenticating with Gmail via OAuth 2.0
  2. Reading unread emails from the inbox
  3. Sending auto-responses for high-confidence matches
  4. Forwarding uncertain requests to a human consultant with full context
"""

import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config


def get_gmail_service():
    """
    Authenticate with Gmail and return the API service object.

    First run: opens a browser for OAuth consent and saves token.json.
    All future runs: loads the saved token silently, refreshing if expired.
    """
    creds = None

    if os.path.exists(config.GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_FILE, config.GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(config.GMAIL_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Gmail credentials file not found at: {config.GMAIL_CREDENTIALS_FILE}\n"
                    "Please follow the setup instructions to download credentials.json "
                    "from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_FILE, config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(config.GMAIL_TOKEN_FILE), exist_ok=True)
        with open(config.GMAIL_TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_unread_messages():
    """
    Fetch all unread messages from the inbox.
    Returns a list of message ID dicts, or an empty list if none.
    """
    try:
        service = get_gmail_service()
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            q="is:unread"
        ).execute()
        messages = result.get("messages", [])
        print(f"[Email] Found {len(messages)} unread message(s).")
        return messages
    except Exception as e:
        print(f"[Email] Error fetching unread messages: {e}")
        return []


def get_message_details(message_id):
    """
    Fetch the full details of a single email.
    Returns a dict with: id, thread_id, sender, subject, body, snippet.
    """
    try:
        service = get_gmail_service()
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        sender = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")
        body = _extract_body(msg["payload"])

        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "sender": sender,
            "subject": subject,
            "body": body,
            "snippet": msg.get("snippet", ""),
        }
    except Exception as e:
        print(f"[Email] Error fetching message {message_id}: {e}")
        return None


def _extract_body(payload):
    """Recursively extract plain-text body from a Gmail message payload."""
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                if "body" in part and part["body"].get("data"):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload["parts"]:
            result = _extract_body(part)
            if result:
                return result

    return ""


def mark_as_read(message_id):
    """Remove the UNREAD label so we don't process this email again."""
    try:
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        print(f"[Email] Error marking message {message_id} as read: {e}")


def send_clarification_email(thread_id, to_email, original_subject, region_list):
    """
    Send an auto-reply asking the requester which region they're based in.
    Sent as a reply to the original email thread so their response lands in the same thread.
    Returns the sent message ID, or None on failure.
    """
    try:
        service = get_gmail_service()
        regions_formatted = "\n".join(f"  • {r}" for r in region_list)
        body_text = (
            f"Dear Consultant,\n\n"
            f"Thank you for your data request. To route your request to the correct team member, "
            f"could you please let us know which region you are based in?\n\n"
            f"Our regions are:\n{regions_formatted}\n\n"
            f"Simply reply to this email with your region and we will process your request promptly.\n\n"
            f"Best regards,\n"
            f"DataDealer"
        )
        message = MIMEMultipart()
        message["To"] = to_email
        message["Subject"] = f"Re: {original_subject}"
        message.attach(MIMEText(body_text, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id}
        ).execute()
        sent_id = sent.get("id")
        print(f"[Email] Clarification email sent to {to_email}. Message ID: {sent_id}")
        return sent_id
    except Exception as e:
        print(f"[Email] Error sending clarification email: {e}")
        return None


def send_auto_response(thread_id, to_email, original_subject, attachment_path, parsed_summary, upload_date=None):
    """
    Send an automatic reply directly to the consultant with the matched file attached.
    Used only for high-confidence matches where the sender is approved.

    Returns the sent message ID, or None on failure.
    """
    try:
        service = get_gmail_service()
        filename = os.path.basename(attachment_path)

        body_text = (
            f"Dear Consultant,\n\n"
            f"Thank you for your request. Please find attached the document you requested: "
            f"{parsed_summary}.\n\n"
            f"Attached: {filename}\n\n"
            f"Please don't hesitate to reach out if you need anything further.\n\n"
            f"Best regards,\n"
            f"DataDealer Automated Response"
        )
        if upload_date:
            body_text += f"\nNote: this is the most recently available version of this document, as of {upload_date}."

        message = MIMEMultipart()
        message["To"] = to_email
        message["Subject"] = f"Re: {original_subject}"
        message.attach(MIMEText(body_text, "plain"))

        with open(attachment_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id}
        ).execute()

        sent_id = sent.get("id")
        print(f"[Email] Auto-response sent to {to_email}. Message ID: {sent_id}")
        return sent_id

    except Exception as e:
        print(f"[Email] Error sending auto-response: {e}")
        return None


def get_sent_message_preview(message_id: str) -> dict:
    """
    Fetch a sent/forwarded Gmail message and return a preview dict.
    Returns {subject, to, date, body, error} — error is None on success.
    """
    try:
        service = get_gmail_service()
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        return {
            "subject": headers.get("Subject", ""),
            "to":      headers.get("To", ""),
            "date":    headers.get("Date", ""),
            "body":    _extract_body(msg["payload"]),
            "error":   None,
        }
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "notFound" in err_str.lower():
            friendly = "Message not found — it may have been deleted from Gmail."
        else:
            friendly = f"Could not load message: {err_str}"
        return {"subject": "", "to": "", "date": "", "body": "", "error": friendly}


def send_queue_notification(to_email, sender_email, subject, parse_summary, status, request_id):
    """
    Send a brief plain-text notification that a request is waiting in the review queue.
    Failures are logged but never raised — a notification must never break the pipeline.
    """
    if not to_email:
        return
    try:
        service = get_gmail_service()
        body_text = (
            f"A new request is waiting in the DataDealer review queue.\n\n"
            f"Sender:     {sender_email}\n"
            f"Subject:    {subject}\n"
            f"Status:     {status}\n"
            f"Summary:    {parse_summary or '(not yet parsed)'}\n"
            f"Request ID: {request_id}\n\n"
            f"Log in to the DataDealer dashboard to review it."
        )
        message = MIMEMultipart()
        message["To"] = to_email
        message["Subject"] = f"[DataDealer] New request in queue — {subject}"
        message.attach(MIMEText(body_text, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        print(f"[Email] Queue notification sent to {to_email} for request {request_id}.")
    except Exception as e:
        print(f"[Email] Failed to send queue notification: {e}")


def forward_to_consultant(original_msg, parsed, matched_file, match_score, reason, to_email=None):
    """
    Forward an uncertain or unapproved request to the human consultant email
    with full context so they can respond manually — no dashboard visit needed.

    Args:
        original_msg:  The original email dict (sender, subject, body, etc.)
        parsed:        The Claude-parsed request dict (may be None if parsing failed)
        matched_file:  The best-matching file dict (may be None if no match found)
        match_score:   The similarity score of the best match (float)
        reason:        Plain-English explanation of why this couldn't be auto-fulfilled

    Returns:
        The sent message ID, or None on failure.
    """
    consultant_email = to_email or config.CONSULTANT_EMAIL
    if not consultant_email:
        print("[Email] CONSULTANT_EMAIL not set in .env — cannot forward uncertain request.")
        return None

    try:
        service = get_gmail_service()

        sender = original_msg.get("sender", "unknown")
        subject = original_msg.get("subject", "(no subject)")
        original_body = original_msg.get("body", "")

        # Build the forwarding email body with all context a human would need
        sections = []

        sections.append("DataDealer has received a data request that requires your attention.")
        sections.append(f"{'─' * 60}")

        sections.append(f"FROM:    {sender}")
        sections.append(f"SUBJECT: {subject}")
        sections.append(f"{'─' * 60}")

        sections.append(f"REASON THIS WAS FORWARDED:\n{reason}")
        sections.append(f"{'─' * 60}")

        if parsed:
            sections.append("WHAT THE AI UNDERSTOOD:")
            sections.append(f"  Summary:     {parsed.get('summary', '—')}")
            sections.append(f"  Asset Class: {parsed.get('asset_class', '—')}")
            sections.append(f"  Region:      {parsed.get('region', '—')}")
            sections.append(f"  Fund/Strategy: {parsed.get('fund_name', '—')}")
            sections.append(f"  Vehicle:     {parsed.get('vehicle', '—')}")
            sections.append(f"  Share Class: {parsed.get('share_class', '—')}")
            sections.append(f"  Data Type:   {parsed.get('data_type', '—')}")
            sections.append(f"  Time Period: {parsed.get('time_period', '—')}")
            sections.append(f"  AI Confidence: {parsed.get('confidence', '—')}")
        else:
            sections.append("AI PARSING: Failed — could not extract structured data from this email.")

        sections.append(f"{'─' * 60}")

        if matched_file:
            sections.append(f"BEST FILE MATCH (score: {match_score:.2f}):")
            sections.append(f"  Filename:    {matched_file.get('filename', '—')}")
            sections.append(f"  Fund:        {matched_file.get('fund_name', '—')}")
            sections.append(f"  Asset Class: {matched_file.get('asset_class', '—')}")
            sections.append(f"  Region:      {matched_file.get('region', '—')}")
            sections.append(f"  Data Type:   {matched_file.get('data_type', '—')}")
            sections.append(f"  Period:      {matched_file.get('time_period', '—')}")
        else:
            sections.append("BEST FILE MATCH: None found above the similarity threshold.")

        sections.append(f"{'─' * 60}")
        sections.append("ORIGINAL EMAIL BODY:")
        sections.append(original_body[:3000] + ("..." if len(original_body) > 3000 else ""))

        body_text = "\n".join(sections)

        # Build the MIME message
        message = MIMEMultipart()
        message["To"] = consultant_email
        message["Subject"] = f"[DataDealer] Uncertain Request — {subject}"
        message.attach(MIMEText(body_text, "plain"))

        # If a file was matched, attach it so the consultant can send it directly
        # if they decide the request is valid
        if matched_file and matched_file.get("file_path"):
            file_path = matched_file["file_path"]
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    attachment = MIMEBase("application", "octet-stream")
                    attachment.set_payload(f.read())
                    encoders.encode_base64(attachment)
                    attachment.add_header(
                        "Content-Disposition",
                        f'attachment; filename="{os.path.basename(file_path)}"'
                    )
                    message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        sent_id = sent.get("id")
        print(f"[Email] Uncertain request forwarded to {consultant_email}. Message ID: {sent_id}")
        return sent_id

    except Exception as e:
        print(f"[Email] Error forwarding to consultant: {e}")
        return None
