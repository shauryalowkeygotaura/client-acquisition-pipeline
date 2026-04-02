"""
modules/reply_handler.py — Phase 4 upgrade

Checks Gmail INBOX for replies from leads.
Classifies each reply → generates a response → sends it → updates sheets.

Requires: GMAIL_ADDRESS, GMAIL_APP_PASSWORD env vars (same as email_sender).
Run this module from the pipeline daily (separate from the main send loop).

CTA Ladder:
  Stage 1 (initial reply):     "Worth a quick 5-min call?"
  Stage 2 (follow-up/warm):    "I can show how this works for your business this week"
  Stage 3 (high intent/book):  Direct cal.com booking link
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

CTA ladder — choose the right level:
  initial → "Worth a quick 5-min Google Meet call?"
  follow_up_1 → "I can walk you through it on a quick Meet call this week — would that work?"
  warm / follow_up_2 → Direct booking link: {booking_link}

Rules per category:
  interested → move toward the meeting. One clear ask.
  neutral → one sharp specific question about their situation, OR offer a 2-min demo clip.
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
        return "Thanks for getting back to me. Worth a quick Google Meet call? — Shaurya"

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
        return "Thanks for getting back to me. Worth a quick Google Meet call? — Shaurya"


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


def _send_reply(to_addr: str, subject: str, body: str) -> bool:
    """Send a reply via Gmail SMTP."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return False
    try:
        msg = MIMEText(body, "plain")
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_addr
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error("Reply send failed to %s: %s", to_addr, e)
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
        return

    # Build email→lead index from sheet
    leads = sheets_writer.get_all_leads()
    email_to_lead = {
        (lead.get("email") or "").lower(): lead
        for lead in leads
        if lead.get("email")
    }

    for msg in inbox:
        sender = msg["from_addr"]
        lead = email_to_lead.get(sender)
        if not lead:
            continue  # Not one of our leads

        slug = lead.get("slug")
        if not slug:
            continue

        # Skip already-dead or already-booked conversations
        stage = lead.get("conversation_stage", "initial")
        if stage in ("dead", "booked", "closed"):
            continue

        log.info("Processing reply from %s (%s)", sender, lead.get("company_name"))

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
            log.info("Replied to %s — stage: %s → %s", sender, stage, new_stage)
        else:
            log.error("Failed to send reply to %s", sender)


def send_follow_ups(max_per_run: int = 10):
    """
    Send follow-up messages to leads who haven't replied after 3 and 7 days.
    Call this daily from the pipeline scheduler.
    Respects follow_up_count (max 2 follow-ups per lead).
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
        follow_up_count = int(lead.get("follow_up_count") or "0")
        reply_status = lead.get("reply_status", "")
        stage = lead.get("conversation_stage", "initial")

        # Skip: no email, already replied, already booked/dead, max follow-ups reached
        if not email or not sent_at_str or reply_status or follow_up_count >= 2:
            continue
        if stage in ("dead", "booked", "closed"):
            continue

        try:
            sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        days_since = (datetime.now(timezone.utc) - sent_at).days

        # Follow-up 1: day 3–4 | Follow-up 2: day 7–9
        is_followup_1_window = follow_up_count == 0 and 3 <= days_since <= 4
        is_followup_2_window = follow_up_count == 1 and 7 <= days_since <= 9

        if not (is_followup_1_window or is_followup_2_window):
            continue

        # Generate a short follow-up nudge
        company = lead.get("company_name", "your business")
        subject = lead.get("email_subject", "following up")
        if follow_up_count == 0:
            body = (
                f"Just wanted to make sure this didn't get buried.\n\n"
                f"Still happy to send a 2-min demo clip showing exactly how this works for "
                f"a {lead.get('niche', 'service')} business — no call needed.\n\n"
                f"Worth it? — Shaurya"
            )
        else:
            body = (
                f"Last nudge, I promise.\n\n"
                f"If covering calls while the hiring gap is open is still a problem, "
                f"I'm happy to show you how it works. If you've sorted it, no worries at all.\n\n"
                f"— Shaurya"
            )

        sent = _send_reply(email, subject, body)
        if sent:
            sheets_writer.increment_follow_up(slug)
            sent_count += 1
            log.info("Follow-up %d sent to %s", follow_up_count + 1, company)
