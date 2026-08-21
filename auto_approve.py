"""
auto_approve.py
Verifies a Venmo payment via Gmail, then calls the local approve endpoint.
"""

import os
import re
import base64
import logging
from datetime import datetime, timezone, timedelta

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

# ── Env vars ──────────────────────────────────────────────────────────────────
ADMIN_URL           = os.environ.get("ADMIN_URL", "https://turnin-license.onrender.com")
ADMIN_PASSWORD      = os.environ.get("ADMIN_PASSWORD", "")
ALERT_EMAIL         = os.environ.get("ALERT_EMAIL", "")
GMAIL_TOKEN         = os.environ.get("GMAIL_TOKEN", "")
GMAIL_REFRESH       = os.environ.get("GMAIL_REFRESH", "")
GMAIL_CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
VENMO_WINDOW_MINS   = int(os.environ.get("VENMO_EMAIL_WINDOW_MINUTES", "30"))

# ── Gmail helpers ─────────────────────────────────────────────────────────────

def _gmail_service():
    creds = Credentials(
        token=GMAIL_TOKEN,
        refresh_token=GMAIL_REFRESH,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _get_email_subject(service, msg_id: str) -> str:
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = msg.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == "subject":
            return h.get("value", "")
    return ""


def _get_email_body(service, msg_id: str) -> str:
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part["body"].get("data", "")
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    data = msg.get("payload", {}).get("body", {}).get("data", "")
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def find_venmo_payment(venmo_display_name: str, expected_amount: float) -> bool:
    """
    Returns True if a Venmo email from venmo@venmo.com arrived in the last
    VENMO_WINDOW_MINS minutes whose subject contains the buyer's name AND
    whose subject or body contains a dollar amount >= expected_amount.
    """
    try:
        service = _gmail_service()
    except Exception as e:
        log.error("Gmail auth failed: %s", e)
        return False

    since = datetime.now(timezone.utc) - timedelta(minutes=VENMO_WINDOW_MINS)
    since_epoch = int(since.timestamp())
    # Fetch ALL recent Venmo emails — no subject filter so we catch every format
    query = 'from:venmo@venmo.com'

    results = service.users().messages().list(userId="me", q=query).execute()
    messages = results.get("messages", [])

    if not messages:
        log.info("No Venmo emails found in the last %d minutes.", VENMO_WINDOW_MINS)
        return False

    name_lower = venmo_display_name.strip().lower()

    for m in messages:
        subject = _get_email_subject(service, m["id"])
        # Name must appear in the subject line
        if name_lower not in subject.lower():
            continue
        # Look for the dollar amount in subject first, then fall back to body
        search_text = subject + " " + _get_email_body(service, m["id"])
        amounts = re.findall(r"\$(\d+(?:\.\d{1,2})?)", search_text)
        for a in amounts:
            if float(a) >= expected_amount:
                log.info("Payment confirmed: %s paid $%s (subject: %s)", venmo_display_name, a, subject)
                return True
        log.info("Name matched in subject but amount not found/insufficient (subject: %s)", subject)

    log.info("No matching Venmo payment for %s / $%.2f", venmo_display_name, expected_amount)
    return False


def send_alert_email(subject: str, body: str):
    """Send yourself an alert email via Gmail API."""
    try:
        service = _gmail_service()
        message = (
            f"From: {ALERT_EMAIL}\r\n"
            f"To: {ALERT_EMAIL}\r\n"
            f"Subject: {subject}\r\n\r\n"
            f"{body}"
        )
        raw = base64.urlsafe_b64encode(message.encode()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info("Alert email sent: %s", subject)
    except Exception as e:
        log.error("Failed to send alert email: %s", e)


def approve_order_by_id(order_id: str) -> bool:
    """
    Calls the existing /admin/approve/<order_id> endpoint directly
    using HTTP Basic Auth — no browser needed.
    """
    import requests as req
    url = f"{ADMIN_URL.rstrip('/')}/admin/approve/{order_id}"
    try:
        resp = req.post(url, auth=("admin", ADMIN_PASSWORD), timeout=15, allow_redirects=True)
        success = resp.status_code == 200 and "approved" in resp.url.lower() or "msg=" in resp.url
        log.info("Approve call status=%s url=%s", resp.status_code, resp.url)
        return success
    except Exception as e:
        log.error("Approve request failed: %s", e)
        return False


def process_payment(venmo_display_name: str, order_id: str, expected_amount: float) -> dict:
    """
    Full flow: verify Gmail payment → call approve endpoint → alert on failure.
    Called from the Flask /api/paid endpoint.
    """
    log.info("Processing: %s order=%s amount=$%.2f", venmo_display_name, order_id, expected_amount)

    # Step 1 — verify payment in Gmail
    paid = find_venmo_payment(venmo_display_name, expected_amount)
    if not paid:
        send_alert_email(
            subject=f"[Turnin] Unverified payment — {venmo_display_name}",
            body=(
                f"{venmo_display_name} clicked 'I've Paid' but no matching Venmo email "
                f"was found for ${expected_amount:.2f} in the last {VENMO_WINDOW_MINS} minutes.\n\n"
                f"Order ID: {order_id}\nPlease verify manually."
            ),
        )
        return {"ok": False, "reason": "payment_not_found"}

    # Step 2 — approve in dashboard
    approved = approve_order_by_id(order_id)
    if not approved:
        send_alert_email(
            subject=f"[Turnin] Payment verified but approve failed — {order_id}",
            body=(
                f"Venmo payment from {venmo_display_name} confirmed, "
                f"but the approve call for order {order_id} failed.\n\nPlease approve manually."
            ),
        )
        return {"ok": False, "reason": "approve_failed"}

    log.info("Successfully approved %s for %s", order_id, venmo_display_name)
    return {"ok": True}
