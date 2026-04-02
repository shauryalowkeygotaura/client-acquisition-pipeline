import logging
import os
import re
import smtplib
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def _clean_email(raw: str) -> str | None:
    """Strip whitespace/newlines and validate format. Returns None if invalid."""
    cleaned = raw.strip()
    return cleaned if _EMAIL_RE.match(cleaned) else None


def build_message(data: dict, from_addr: str) -> MIMEText:
    msg = MIMEText(data["email_body"], "plain")
    msg["From"] = from_addr
    msg["To"] = data["email"]
    msg["Subject"] = data["email_subject"]
    return msg


def send(data: dict) -> bool:
    raw_email = data.get("email")
    if not raw_email:
        return False

    email = _clean_email(raw_email)
    if not email:
        log.warning("Skipping invalid email address: %r", raw_email)
        return False

    # Overwrite with cleaned value so build_message uses it
    data = {**data, "email": email}

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD env vars not set — skipping email.")
        return False

    msg = build_message(data, from_addr=GMAIL_ADDRESS)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail authentication failed. Check GMAIL_APP_PASSWORD.")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        for recipient, (code, msg_bytes) in e.recipients.items():
            log.error("Recipient refused — %s (code %d): %s", recipient, code,
                      msg_bytes.decode(errors="replace"))
        return False
    except smtplib.SMTPException as e:
        log.error("SMTP error sending to %s: %s", email, e)
        return False
    except OSError as e:
        log.error("Network error sending email to %s: %s", email, e)
        return False

    return True
