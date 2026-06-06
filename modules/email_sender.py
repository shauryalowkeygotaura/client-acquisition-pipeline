import json
import logging
import os
import re
import secrets
import smtplib
from datetime import datetime, timezone
from email.charset import QP, Charset
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
MAX_PER_ACCOUNT = 50  # Gmail cold email best practice: ≤50/inbox/day

# Default MIMEText("plain", "utf-8") would base64-encode the body, which
# (a) breaks downstream payload inspection and (b) gets harsher spam scoring
# than quoted-printable on Gmail's promo filter.
_BODY_CHARSET = Charset("utf-8")
_BODY_CHARSET.body_encoding = QP

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# In-memory send counter per account per day.
# Key: "address|YYYY-MM-DD" → count sent today.
# Resets naturally on process restart (daily runs).
_send_counts: dict[str, int] = {}


def _load_accounts() -> list[dict]:
    """
    Load Gmail sender accounts from env.

    GMAIL_ACCOUNTS = JSON array:
        [{"address": "you@gmail.com", "password": "app-pw"}, ...]

    Falls back to the original GMAIL_ADDRESS + GMAIL_APP_PASSWORD pair
    so existing setups keep working without any config change.
    """
    raw = os.getenv("GMAIL_ACCOUNTS")
    if raw:
        try:
            accounts = json.loads(raw)
            valid = [a for a in accounts if a.get("address") and a.get("password")]
            if valid:
                return valid
        except json.JSONDecodeError:
            log.error("GMAIL_ACCOUNTS is not valid JSON — falling back to GMAIL_ADDRESS")

    addr = os.getenv("GMAIL_ADDRESS")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if addr and pwd:
        return [{"address": addr, "password": pwd}]
    return []


def _pick_account(accounts: list[dict]) -> dict | None:
    """Return the account with the fewest sends today, or None if all are maxed.

    v3: each account's effective cap is min(MAX_PER_ACCOUNT, warmup_cap).
    Warmup cap ramps 10 → 25 → 50/day across the first 3 weeks of an account's
    life. Legacy accounts without a warmup row default to MAX_PER_ACCOUNT.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    best = None
    best_count = MAX_PER_ACCOUNT + 1
    for acct in accounts:
        key = f"{acct['address']}|{today}"
        count = _send_counts.get(key, 0)
        # v3 warmup-adjusted cap
        try:
            from . import sender_warmup
            warmup_cap = sender_warmup.daily_cap_for(acct["address"])
        except Exception as e:
            log.debug("sender_warmup unavailable (%s) — using MAX_PER_ACCOUNT", e)
            warmup_cap = MAX_PER_ACCOUNT
        effective_cap = min(MAX_PER_ACCOUNT, warmup_cap)
        if count < effective_cap and count < best_count:
            best = acct
            best_count = count
    return best


def _increment_count(address: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{address}|{today}"
    _send_counts[key] = _send_counts.get(key, 0) + 1


def _clean_email(raw: str) -> str | None:
    cleaned = raw.strip()
    return cleaned if _EMAIL_RE.match(cleaned) else None


def generate_opt_out_token() -> str:
    """Generate a short URL-safe token for the unsubscribe footer."""
    return secrets.token_urlsafe(16)


def _unsubscribe_footer(token: str) -> str:
    return f"\n\n---\nTo stop receiving these emails, reply with STOP (ref: {token[:8]})."


def build_message(
    data: dict,
    from_addr: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEText:
    body = data["email_body"]
    opt_out = data.get("opt_out_token", "")
    if opt_out:
        body += _unsubscribe_footer(opt_out)

    # Plain text only: HTML triggers Gmail promo-tab routing and stricter
    # Outlook spam scoring. Body uses real \n paragraph breaks (SMTP preserves).
    msg = MIMEText(body, "plain", _BODY_CHARSET)
    msg["From"] = from_addr
    msg["To"] = data["email"]
    msg["Subject"] = data["email_subject"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()

    # Threading headers — makes follow-ups appear in the same Gmail thread
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    return msg


def send(
    data: dict,
    in_reply_to: str | None = None,
    references: str | None = None,
    force_account: str | None = None,
) -> tuple[bool, str, str]:
    """
    Send an email. Returns (success, message_id, sender_address).

    Args:
        data:          Lead dict — must have "email", "email_body", "email_subject".
        in_reply_to:   Message-ID of email 1, so follow-ups thread in Gmail.
        references:    Same as in_reply_to for simple chains.
        force_account: Use this exact address (for follow-ups that must match
                       the original sender account).
    """
    raw_email = data.get("email")
    if not raw_email:
        return False, "", ""

    email = _clean_email(raw_email)
    if not email:
        print(f"      [EMAIL ERROR] Invalid email format: {raw_email}")
        log.warning("Skipping invalid email: %r", raw_email)
        return False, "", ""

    # Defense in depth — researcher already filters generics, but if anything
    # slipped through (Apollo edge case, hand-imported lead) refuse to send.
    try:
        from . import researcher
        if researcher.is_generic_mailbox(email):
            print(f"      [EMAIL SKIP] {email} is a role mailbox, not a person")
            log.info("Skipping role mailbox: %s", email)
            return False, "", ""
    except Exception:
        pass

    # Optional pre-flight SMTP probe — catches typo'd or dead mailboxes.
    if os.getenv("SMTP_VERIFY_BEFORE_SEND") == "1":
        try:
            from . import email_finder
            if not email_finder.verify(email):
                print(f"      [EMAIL SKIP] {email} failed SMTP RCPT verification")
                log.info("SMTP probe rejected %s — skipping send", email)
                return False, "", ""
        except Exception as e:
            log.debug("SMTP verify error (non-fatal): %s", e)

    data = {**data, "email": email}

    accounts = _load_accounts()
    if not accounts:
        print("      [EMAIL ERROR] No Gmail accounts configured. Check .env file.")
        log.error("No Gmail accounts configured. Set GMAIL_ACCOUNTS or GMAIL_ADDRESS.")
        return False, "", ""

    if force_account:
        acct = next((a for a in accounts if a["address"] == force_account), None)
        if not acct:
            print(f"      [EMAIL ERROR] Force account {force_account} not found in config.")
            log.error("force_account %s not in GMAIL_ACCOUNTS — skipping.", force_account)
            return False, "", ""
    else:
        acct = _pick_account(accounts)
        if not acct:
            print("      [EMAIL ERROR] Daily limit reached for all Gmail accounts.")
            log.warning("All Gmail accounts at daily limit (%d/day). Skipping send.", MAX_PER_ACCOUNT)
            return False, "", ""

    msg = build_message(data, from_addr=acct["address"],
                        in_reply_to=in_reply_to, references=references)
    sent_message_id = msg["Message-ID"]

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(acct["address"], acct["password"])
            server.send_message(msg)
        _increment_count(acct["address"])
        # v3: record this send to the warmup tab. Best-effort — never blocks.
        try:
            from . import sender_warmup
            sender_warmup.record_send(acct["address"])
        except Exception as e:
            log.debug("sender_warmup.record_send failed (%s) — non-fatal", e)
        return True, sent_message_id, acct["address"]
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail auth failed for %s. Check app password.", acct["address"])
        return False, "", ""
    except smtplib.SMTPRecipientsRefused as e:
        for recipient, (code, msg_bytes) in e.recipients.items():
            log.error("Recipient refused — %s (code %d): %s",
                      recipient, code, msg_bytes.decode(errors="replace"))
        return False, "", ""
    except smtplib.SMTPException as e:
        log.error("SMTP error sending to %s: %s", email, e)
        return False, "", ""
    except OSError as e:
        log.error("Network error sending to %s: %s", email, e)
        return False, "", ""
