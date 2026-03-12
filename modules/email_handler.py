"""
email_handler.py — Gmail API integration
─────────────────────────────────────────
Handles three jobs:
  1. Authenticating with Gmail via OAuth 2.0
  2. Reading unread emails from the inbox
  3. Creating draft replies with file attachments (NOT sending — a human must send)
"""

import os
import base64
import json
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

    How this works:
      - First run: opens a browser window asking you to log into the Gmail inbox
        and grant permission. The resulting token is saved to credentials/token.json.
      - All future runs: silently loads the saved token. If it's expired, it
        refreshes automatically (no browser popup needed).
    """
    creds = None

    # Load a previously saved token if it exists
    if os.path.exists(config.GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_FILE, config.GMAIL_SCOPES)

    # If there's no valid token, start the OAuth login flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token exists but expired — refresh it silently
            creds.refresh(Request())
        else:
            # No token at all — open the browser for first-time login
            if not os.path.exists(config.GMAIL_CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Gmail credentials file not found at: {config.GMAIL_CREDENTIALS_FILE}\n"
                    "Please follow the setup instructions to download credentials.json "
                    "from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_FILE, config.GMAIL_SCOPES
            )
            # run_local_server opens a browser tab for the OAuth consent screen
            creds = flow.run_local_server(port=0)

        # Save the token for next time
        os.makedirs(os.path.dirname(config.GMAIL_TOKEN_FILE), exist_ok=True)
        with open(config.GMAIL_TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_unread_messages():
    """
    Fetch all unread messages from the inbox.
    Returns a list of message ID dicts like [{'id': '...', 'threadId': '...'}].
    Returns an empty list if there are no unread messages.
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
    Fetch the full details of a single email by its ID.
    Returns a dict with: id, thread_id, sender, subject, body, snippet.
    Returns None if the message can't be fetched.
    """
    try:
        service = get_gmail_service()
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        # Extract headers (From, Subject, etc.)
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}

        sender = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")

        # Extract the plain-text body
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
    """
    Recursively extract the plain-text body from a Gmail message payload.
    Gmail nests the body inside parts, so we need to walk the tree.
    """
    # If this part has a body with data, decode it
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # If this part has sub-parts, recurse through them looking for text/plain
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                if "body" in part and part["body"].get("data"):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # If no text/plain found, try any part recursively
        for part in payload["parts"]:
            result = _extract_body(part)
            if result:
                return result

    return ""


def mark_as_read(message_id):
    """
    Remove the UNREAD label from a message so we don't process it again.
    """
    try:
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        print(f"[Email] Error marking message {message_id} as read: {e}")


def create_draft_reply(thread_id, to_email, original_subject, attachment_path, parsed_summary):
    """
    Create a Gmail DRAFT reply with the matched file attached.

    ⚠️  IMPORTANT: This creates a DRAFT only — it does NOT send the email.
    A human must open Gmail, find the draft, review it, and click Send.

    Args:
        thread_id:        The Gmail thread ID to reply in
        to_email:         The consultant's email address
        original_subject: The subject line of the original email
        attachment_path:  Local path to the file to attach
        parsed_summary:   A short description of what was requested (for the reply body)

    Returns:
        The Gmail draft ID (str) so we can store it in the database, or None on error.
    """
    try:
        service = get_gmail_service()

        # Build the email body text
        filename = os.path.basename(attachment_path)
        body_text = (
            f"Dear Consultant,\n\n"
            f"Thank you for your request. Please find attached the information you requested: "
            f"{parsed_summary}.\n\n"
            f"Attached file: {filename}\n\n"
            f"Please do not hesitate to reach out if you need anything further.\n\n"
            f"Best regards,\n"
            f"DataDealer — Automated Data Response\n\n"
            f"──────────────────────────────────────\n"
            f"⚠️  This is a draft prepared by DataDealer. A member of our team will "
            f"review and send this response shortly."
        )

        # Build the MIME message (the email format Gmail understands)
        message = MIMEMultipart()
        message["To"] = to_email
        message["Subject"] = f"Re: {original_subject}"

        # Attach the text body
        message.attach(MIMEText(body_text, "plain"))

        # Attach the file
        with open(attachment_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"'
            )
            message.attach(attachment)

        # Encode the entire message to base64 (Gmail API requirement)
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        # Create the draft — note we pass threadId to keep it in the same email thread
        draft_body = {
            "message": {
                "raw": raw_message,
                "threadId": thread_id,
            }
        }

        draft = service.users().drafts().create(
            userId="me",
            body=draft_body
        ).execute()

        draft_id = draft.get("id")
        print(f"[Email] Draft created successfully. Draft ID: {draft_id}")
        return draft_id

    except Exception as e:
        print(f"[Email] Error creating draft reply: {e}")
        return None
