"""
modules/reply_handler.py — Phase 4 upgrade

Checks Gmail INBOX for replies from leads.
Classifies each reply → generates a response → sends it → updates sheets.

Requires: GMAIL_ADDRESS, GMAIL_APP_PASSWORD env vars (same as email_sender).
Run this module from the pipeline daily (separate from the main send loop).

CTA Ladder (Hormozi sequence — never skip a step):
  Stage 1 (initial reply):     Offer the 2-min clip. No call ask.
  Stage 2 (follow_up_1):       Follow up on the clip. Ask one question about their situation.
  Stage 3 (warm):              "Worth a quick 5-min call?" — first time a call is mentioned.
  Stage 4 (follow_up_2):       Direct Google Meet booking link.
"""
import email as email_lib
import imaplib
import json
import logging
import os
import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from openai import OpenAI

from config import LLM_MODEL, LLM_BASE_URL
from modules import sheets_writer

log = logging.getLogger(__name__)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
BOOKING_LINK = os.getenv("BOOKING_LINK", "https://meet.google.com")  # Google Meet or Calendly link

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ── Reply classification ─────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """
You are classifying a reply to a cold outreach email about an AI voice receptionist.

Reply text:
{reply}

Classify into exactly ONE of these categories:
- interested: they expressed curiosity, asked a question, want to know more, or said yes
- neutral: acknowledged but gave no clear signal (e.g. "thanks", "noted", "will think about it")
- objection: pushed back with a specific concern (cost, trust, timing, already have a solution, etc.)
- not_relevant: completely off-topic, auto-reply, wrong person, spam, or unsubscribe

Also extract:
- objection_type: if objection, what is the core concern? (cost/timing/trust/existing_solution/other). Empty string otherwise.
- brief_summary: 1 sentence summary of what they said.

Return ONLY valid JSON with keys: category, objection_type, brief_summary.
""".strip()


def _classify_reply(reply_text: str) -> dict:
    """Call Groq LLM to classify a reply. Returns {category, objection_type, brief_summary}."""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set — skipping classification")
        return {"category": "neutral", "objection_type": "", "brief_summary": reply_text[:100]}

    client = OpenAI(api_key=GROQ_API_KEY, base_url=LLM_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(reply=reply_text[:2000])}],
            temperature=0.2,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as e:
        log.error("classify_reply LLM failed: %s", e)
        return {"category": "neutral", "objection_type": "", "brief_summary": ""}


# ── Response generation ──────────────────────────────────────────────────────

_OBJECTION_REBUTTALS = {
    "cost": (
        "Completely fair — most people ask this first. "
        "The short answer: it's less than one hour of a receptionist's pay per day, "
        "with no sick days, no training time, and no hiring cost. "
        "Want me to send a quick breakdown so you can compare properly?"
    ),
    "timing": (
        "Totally makes sense — the hiring gap is exactly when this is most useful. "
        "It takes less than a day to set up and can hand off cleanly once you hire. "
        "Want me to send a 2-min clip so you can see what it looks like in practice?"
    ),
    "trust": (
        "Reasonable. I don't expect you to take my word for it. "
        "Want me to send a short recording of it handling a call for a similar business? "
        "You can hear exactly how it sounds before deciding anything."
    ),
    "existing_solution": (
        "Got it — what are you using currently? "
        "I ask because most setups still miss calls after hours or during peak times. "
        "If yours doesn't, it's genuinely not worth switching. "
        "Happy to send a quick clip so you can compare."
    ),
    "other": (
        "Fair point. "
        "Happy to answer any specific questions — or I can just send a 2-min clip "
        "so you can see exactly what it does before we go further. "
        "Would that be useful?"
    ),
}

_RESPONSE_PROMPT = """
You are Shaurya — a developer who builds AI voice receptionists for small businesses.
Write a reply FROM Shaurya TO the business owner at {company_name}.

Context:
- Business type: {niche}
- Their reply (summary): {brief_summary}
- Their full reply: {reply_text}
- Reply category: {category}
- Objection type (if any): {objection_type}
- Conversation stage: {stage}

CTA ladder — Hormozi sequence. NEVER skip a step. NEVER ask for a call before stage warm.
  initial     → offer the 2-min clip only. No call mention. No booking link.
  follow_up_1 → ask one specific question about their situation OR follow up on the clip.
                Still no call ask.
  warm        → "Worth a quick 5-min call?" — first and only time a call is suggested.
  follow_up_2 → drop the direct booking link: {booking_link}

Rules per category:
  interested → match the current stage CTA above. Never jump ahead.
  neutral → one sharp specific question about their situation. No clip, no ask.
  objection:
    cost: less than one hour of a receptionist's wage per day. No sick leave, no notice period.
    timing: takes less than a day to set up; most useful during the hiring gap.
    trust: offer a recording of it handling a real call for a similar business.
    existing_solution: ask what they use — most setups still miss after-hours calls.
    other: acknowledge the specific concern, then ask if a quick clip would help.

Hard rules:
- 60–100 words max. No buzzwords. No "I hope". Sound like a real human.
- "I" = Shaurya. "You/your" = the business owner.
- Sign off: — Shaurya

Return ONLY the reply text. No JSON, no subject line, no explanation.
""".strip()


def _generate_response(lead: dict, classification: dict, reply_text: str = "") -> str:
    """LLM-powered response for every category. Hardcoded rebuttals are fallback only."""
    category = classification.get("category", "neutral")
    objection_type = classification.get("objection_type", "other")
    stage = lead.get("conversation_stage", "initial")

    if not GROQ_API_KEY:
        if category == "objection":
            return _OBJECTION_REBUTTALS.get(objection_type, _OBJECTION_REBUTTALS["other"])
        return "Thanks for getting back to me. Happy to send a 2-min clip if that would help — just say the word. — Shaurya"

    client = OpenAI(api_key=GROQ_API_KEY, base_url=LLM_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": _RESPONSE_PROMPT.format(
                company_name=lead.get("company_name", "your business"),
                niche=lead.get("niche", "service business"),
                brief_summary=classification.get("brief_summary", ""),
                reply_text=reply_text[:500],
                category=category,
                objection_type=objection_type or "none",
                stage=stage,
                booking_link=BOOKING_LINK,
            )}],
            temperature=0.55,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
        raise ValueError("empty LLM response")
    except Exception as e:
        log.error("generate_response LLM failed: %s — using fallback", e)
        if category == "objection":
            return _OBJECTION_REBUTTALS.get(objection_type, _OBJECTION_REBUTTALS["other"])
        return "Thanks for getting back to me. Happy to send a 2-min clip if that would help — just say the word. — Shaurya"


# ── Gmail IMAP + SMTP ────────────────────────────────────────────────────────

def _fetch_inbox_replies(since_date: str) -> list[dict]:
    """
    Fetch emails received since since_date (DD-Mon-YYYY format, e.g. '01-Apr-2026').
    Returns list of {from_addr, subject, body, message_id}.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("Gmail credentials not set — cannot check inbox")
        return []

    replies = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        _, data = mail.search(None, f'(SINCE "{since_date}" NOT FROM "{GMAIL_ADDRESS}")')
        msg_ids = data[0].split()

        for mid in msg_ids:
            _, msg_data = mail.fetch(mid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)

            from_addr = email_lib.utils.parseaddr(msg.get("From", ""))[1].lower()
            subject = msg.get("Subject", "")
            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="replace")

            # Strip quoted reply history (keep only the new content)
            body = re.split(r'\n[>\-]{3,}|\nOn .+ wrote:', body)[0].strip()

            replies.append({
                "from_addr": from_addr,
                "subject": subject,
                "body": body[:3000],
                "message_id": msg.get("Message-ID", ""),
            })

        mail.logout()
    except Exception as e:
        log.error("IMAP fetch failed: %s", e)

    return replies


def _send_reply(
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    sender_account: str | None = None,
) -> bool:
    """
    Send a reply via Gmail SMTP.
    - in_reply_to / references: thread the email inside the original conversation.
    - sender_account: send from the same address that sent email 1 (rotation safety).
    """
    from email.utils import formatdate, make_msgid

    # Resolve credentials: prefer the stored sender_account, fall back to env default
    from_addr = sender_account or GMAIL_ADDRESS
    password = None
    if sender_account:
        # Look up password from GMAIL_ACCOUNTS JSON
        import json as _json
        raw = os.getenv("GMAIL_ACCOUNTS")
        if raw:
            try:
                accounts = _json.loads(raw)
                match = next((a for a in accounts if a.get("address") == sender_account), None)
                if match:
                    password = match.get("password")
            except Exception:
                pass
    if not password:
        password = GMAIL_APP_PASSWORD

    if not from_addr or not password:
        log.error("No Gmail credentials available for reply send.")
        return False

    from .security_utils import get_audit_log, redact_text
    audit = get_audit_log()
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid()
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(from_addr, password)
            server.send_message(msg)
        audit.append("reply_handler", "send", to_addr, ok=True,
                     detail={"channel": "email", "kind": "reply_or_followup",
                             "message_id": msg["Message-ID"], "sender": from_addr,
                             "in_reply_to": in_reply_to or ""})
        return True
    except Exception as e:
        log.error("Reply send failed to %s: %s", to_addr, redact_text(str(e)))
        audit.append("reply_handler", "send", to_addr, ok=False,
                     detail={"channel": "email", "kind": "reply_or_followup",
                             "error": redact_text(str(e))})
        return False


# ── Next conversation stage ──────────────────────────────────────────────────

def _next_stage(current: str, category: str) -> str:
    if category == "not_relevant":
        return "dead"
    if current == "initial":
        return "follow_up_1"
    if current == "follow_up_1":
        return "follow_up_2"
    if category == "interested":
        return "warm"
    return current


# ── Main entry point ─────────────────────────────────────────────────────────

def run(since_days: int = 7):
    """
    Check Gmail for replies from leads in the sheet.
    since_days: how far back to search (default 7 days).
    """
    from datetime import timedelta
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    since_date = since_dt.strftime("%d-%b-%Y")

    inbox = _fetch_inbox_replies(since_date)
    if not inbox:
        log.info("reply_handler: no new inbox messages since %s", since_date)
        return {"inbox_msgs": 0, "replies_handled": 0, "optouts": 0}

    # Build email→lead index from sheet
    leads = sheets_writer.get_all_leads()
    email_to_lead = {
        (lead.get("email") or "").lower(): lead
        for lead in leads
        if lead.get("email")
    }

    replies_handled = 0
    optouts = 0

    for msg in inbox:
        sender = msg["from_addr"]
        lead = email_to_lead.get(sender)
        if not lead:
            continue  # Not one of our leads

        slug = lead.get("slug")
        if not slug:
            continue

        # Skip already-dead, booked, or opted-out conversations
        stage = lead.get("conversation_stage", "initial")
        if stage in ("dead", "booked", "closed"):
            continue
        if lead.get("opted_out", "no") == "yes":
            continue

        log.info("Processing reply from %s (%s)", sender, lead.get("company_name"))

        # Detect unsubscribe requests (reply body contains STOP)
        if re.search(r'\bSTOP\b', msg["body"], re.IGNORECASE):
            log.info("Opt-out received from %s — marking opted_out=yes", sender)
            sheets_writer.update_field(slug, "opted_out", "yes")
            sheets_writer.update_reply(slug, "not_relevant", "dead")
            optouts += 1
            continue

        classification = _classify_reply(msg["body"])
        category = classification.get("category", "neutral")
        objection_type = classification.get("objection_type", "")

        if category == "not_relevant":
            sheets_writer.update_reply(slug, "not_relevant", "dead")
            continue

        response_text = _generate_response(lead, classification, reply_text=msg["body"])
        sent = _send_reply(sender, msg["subject"], response_text)

        if sent:
            new_stage = _next_stage(stage, category)
            sheets_writer.update_reply(
                slug,
                reply_status=category,
                conversation_stage=new_stage,
                objection_type=objection_type,
            )
            replies_handled += 1
            log.info("Replied to %s — stage: %s → %s", sender, stage, new_stage)
        else:
            log.error("Failed to send reply to %s", sender)

    return {
        "inbox_msgs": len(inbox),
        "replies_handled": replies_handled,
        "optouts": optouts,
    }


# v3 per-channel follow-up caps. SMB B2B benchmarks show 5–7 touchpoints
# to first response, so the legacy cap of 3 was leaving money on the table.
# Override via env: EMAIL_FOLLOWUP_MAX=5 etc.
EMAIL_FOLLOWUP_MAX = int(os.getenv("EMAIL_FOLLOWUP_MAX", "5"))
WHATSAPP_FOLLOWUP_MAX = int(os.getenv("WHATSAPP_FOLLOWUP_MAX", "3"))
INSTAGRAM_FOLLOWUP_MAX = int(os.getenv("INSTAGRAM_FOLLOWUP_MAX", "2"))


def _safe_int(value) -> int:
    """Parse a sheet cell into an int, tolerating None/'' /non-numeric → 0."""
    try:
        return int(value) if value not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def _email_followup_count(lead: dict) -> int:
    """Take the max of new per-channel counter and legacy aggregate so partially
    migrated rows are handled correctly. Both fields are kept in sync going
    forward via sheets_writer.increment_channel_followup."""
    try:
        new = int(lead.get("email_follow_up_count") or 0)
    except (ValueError, TypeError):
        new = 0
    try:
        legacy = int(lead.get("follow_up_count") or 0)
    except (ValueError, TypeError):
        legacy = 0
    return max(new, legacy)


def send_follow_ups(max_per_run: int = 10):
    """
    Send follow-up messages to leads who haven't replied.

    v3: cap is now per-channel. Email defaults to 5 touchpoints (was 3),
    matching SMB B2B benchmark of 5–7 to first response. Day windows
    extended accordingly. Legacy follow_up_count is still incremented so
    existing analytics/optimizer reads keep working.
    """
    from datetime import timedelta
    leads = sheets_writer.get_all_leads()
    sent_count = 0

    for lead in leads:
        if sent_count >= max_per_run:
            break

        slug = lead.get("slug")
        email = lead.get("email")
        sent_at_str = lead.get("sent_at", "")
        follow_up_count = _email_followup_count(lead)
        reply_status = lead.get("reply_status", "")
        stage = lead.get("conversation_stage", "initial")

        opted_out = lead.get("opted_out", "no")
        if not email or not sent_at_str or reply_status or follow_up_count >= EMAIL_FOLLOWUP_MAX:
            continue
        if stage in ("dead", "booked", "closed") or opted_out == "yes":
            continue

        try:
            sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        days_since = (datetime.now(timezone.utc) - sent_at).days

        # v3 windows: day 3–4 | 7–9 | 12–14 | 18–21 | 28–30
        windows = {
            0: (3, 4),
            1: (7, 9),
            2: (12, 14),
            3: (18, 21),
            4: (28, 30),
        }
        win = windows.get(follow_up_count)
        if not win or not (win[0] <= days_since <= win[1]):
            continue

        company = lead.get("company_name", "your business")
        niche = lead.get("niche", "service")
        location = lead.get("location", "your area")
        subject = lead.get("email_subject", "following up")

        if follow_up_count == 0:
            body = (
                f"Wanted to add something useful to this.\n\n"
                f"Most {niche} businesses in {location} miss 20–35% of inbound calls during a "
                f"hiring gap — mostly afternoons when existing staff are with patients or clients.\n\n"
                f"Happy to pull a rough missed-call estimate for {company} specifically if that "
                f"would be useful. No call needed.\n\n"
                f"— Shaurya"
            )
        elif follow_up_count == 1:
            body = (
                f"Quick follow-up with something concrete.\n\n"
                f"A {niche} practice in a similar situation used a voice agent during their hiring gap. "
                f"They ended up keeping it after they hired someone — it was catching after-hours calls "
                f"they'd never recovered before.\n\n"
                f"If the timing's off, completely fine. If calls are still slipping, worth a 2-min look.\n\n"
                f"— Shaurya"
            )
        elif follow_up_count == 2:
            body = (
                f"If covering the front desk isn't the problem right now, ignore this completely.\n\n"
                f"If it still is — happy to send a 2-min clip, no strings.\n\n"
                f"— Shaurya"
            )
        elif follow_up_count == 3:
            # v3 follow-up 4 — concrete operational value, no ask.
            # Uses a REAL count from the 'unreachable_reviews' sheet field when it
            # has been populated (see scripts/enrich_reviews.py). Falls back to a
            # non-falsifiable generic line when the field is absent or zero — we
            # never invent a number.
            n_unreachable = _safe_int(lead.get("unreachable_reviews"))
            if n_unreachable > 0:
                noun = "review" if n_unreachable == 1 else "reviews"
                body = (
                    f"Quick one, not following up, just sharing.\n\n"
                    f"I went through {company}'s public reviews and {n_unreachable} {noun} mention not "
                    f"being able to get through by phone. That's usually the visible 5% of the missed-call "
                    f"iceberg, the rest never leave a trace.\n\n"
                    f"If you ever want a rough estimate of what that's costing, happy to send it. No call.\n\n"
                    f"— Shaurya"
                )
            else:
                body = (
                    f"Quick one, not following up, just sharing.\n\n"
                    f"Whenever I look at {niche} practices, the reviews mentioning \"couldn't get through\" "
                    f"are usually the visible 5% of the missed-call iceberg. The rest never leave a trace.\n\n"
                    f"If you ever want a rough estimate of what {company} is actually missing, happy to send it. No call.\n\n"
                    f"— Shaurya"
                )
        else:
            # v3 follow-up 5 — the actual breakup, lowest friction
            body = (
                f"Closing the loop on this thread.\n\n"
                f"If the front desk is sorted, ignore. If it's not and you'd ever want to compare "
                f"options, my line is open — no follow-up from me after this.\n\n"
                f"— Shaurya"
            )

        msg_id = lead.get("message_id") or None
        sender_acct = lead.get("sender_account") or None

        sent = _send_reply(
            email, subject, body,
            in_reply_to=msg_id,
            references=msg_id,
            sender_account=sender_acct,
        )
        if sent:
            # v3: increment per-channel counter (also syncs legacy follow_up_count)
            try:
                sheets_writer.increment_channel_followup(slug, "email")
            except Exception as e:
                # Fall back to legacy increment if per-channel helper is missing
                log.warning("increment_channel_followup failed (%s) — using legacy", e)
                sheets_writer.increment_follow_up(slug)
            sent_count += 1
            log.info("Email follow-up %d/%d sent to %s (threaded=%s)",
                     follow_up_count + 1, EMAIL_FOLLOWUP_MAX, company, bool(msg_id))

    return sent_count
