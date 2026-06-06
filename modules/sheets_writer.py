import json
import logging
import os
import time
from datetime import datetime, timezone

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    # ── Original columns (preserved exactly — existing data stays intact) ──
    "slug", "company_name", "website", "domain", "contact_name",
    "email", "linkedin_url", "vapi_prompt", "email_subject",
    "email_body", "linkedin_msg", "linkedin_post", "email_sent", "linkedin_sent",
    "status", "sent_at", "replied_at", "vapi_assistant_id",
    # ── Phase 1/2 enrichment (appended — won't shift existing columns) ──
    "niche", "lead_score", "hiring_urgency", "pain_signal",
    # ── Phase 3 message tracking ──
    "message_variant_id", "channel_used",
    # ── Phase 4 reply handling ──
    "reply_status", "conversation_stage", "objection_type", "follow_up_count",
    # ── Phase 5 outcome tracking ──
    "booked_call", "closed_client",
    # ── Email rotation + threading + unsubscribe ──
    "message_id", "opt_out_token", "sender_account", "opted_out",
    # ── Phase 6 person-level personalization ──
    "person_hook", "company_hook",
    # ── WhatsApp sequence tracking ──
    "phone", "whatsapp_stage", "whatsapp_reply_at",
    # ── v3 digital maturity signals (regex/heuristic, populated by enricher) ──
    "pms_signal", "whatsapp_presence", "instagram_presence", "online_booking",
    "multi_location", "language_signal", "review_velocity", "budget_proxy",
    # ── v3 dual scoring (pain + adoption → combined lead_score) ──
    "digital_maturity_score", "adoption_score", "pain_score",
    # ── v3 Instagram DM channel (primary for India SMB) ──
    "instagram_handle", "instagram_msg", "instagram_sent",
    # ── v3 per-channel follow-up counters (replaces single follow_up_count) ──
    "email_follow_up_count", "whatsapp_follow_up_count", "instagram_follow_up_count",
    # ── v3 close attribution + LTV ──
    "monthly_value", "cancelled_at", "churn_reason", "ltv", "deployment_clinic_id",
    # ── 2026-05-23 inbound integration (personal-brand → sales pipeline) ──
    "inbound_source", "attribution_post_id",
    # ── 2026-05-29 local-business harvest (Maps source) ──
    # Lead-specific opening line + the Google Maps listing the lead came from.
    "icebreaker", "rating", "review_count", "maps_url",
    # ── 2026-05-30 merged source: where the lead came from + hot hiring flag ──
    "source_type", "hiring_now",
]

NICHE_ANALYTICS_HEADERS = [
    "niche", "total_sent", "total_replies", "total_booked_calls",
    "total_clients", "reply_rate", "booked_call_rate", "conversion_rate",
]


def get_sheet(tab: str = "leads", retries: int = 4, backoff: float = 2.0):
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set.")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_ID env var is not set.")
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    client = gspread.authorize(creds)
    for attempt in range(retries):
        try:
            return client.open_by_key(sheet_id).worksheet(tab)
        except APIError as e:
            status = e.response.status_code if hasattr(e, "response") else 0
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = backoff ** attempt
                log.warning("Sheets API %s on attempt %d — retrying in %.1fs", status, attempt + 1, wait)
                time.sleep(wait)
            else:
                raise


def get_all_leads() -> list[dict]:
    sheet = get_sheet("leads")
    values = sheet.get_all_values()
    if not values:
        return []
    
    # Use our known HEADERS for mapping
    # Note: Sheet might have more/fewer columns, we map what we can
    sheet_headers = values[0]
    data_rows = values[1:]
    
    leads = []
    for row in data_rows:
        lead = {}
        for idx, header in enumerate(HEADERS):
            if idx < len(row):
                lead[header] = row[idx]
            else:
                lead[header] = ""
        leads.append(lead)
    return leads


def domain_exists(domain: str, existing: list[dict]) -> bool:
    if not domain:
        return False
    target = domain.lower()
    return any(row.get("domain", "").lower() == target for row in existing)


def build_row(data: dict) -> list:
    # Inbound leads (modules/inbound_intake.py) pass explicit values for
    # status / sent_at / conversation_stage / reply_status / channel_used and
    # use the key "website" instead of "company_website". data.get(..., <default>)
    # lets outbound keep its defaults while letting inbound's fields land.
    return [
        # Original columns
        data.get("slug", ""),
        data.get("company_name", ""),
        data.get("company_website") or data.get("website", ""),
        data.get("domain", ""),
        data.get("poster_name") or data.get("contact_name", ""),
        data.get("email", ""),
        data.get("linkedin_url", ""),
        data.get("vapi_prompt", ""),
        data.get("email_subject", ""),
        data.get("email_body", ""),
        data.get("linkedin_msg", ""),
        data.get("linkedin_post", ""),
        "FALSE",   # email_sent
        "FALSE",   # linkedin_sent
        data.get("status", "pending"),
        data.get("sent_at", ""),
        "",        # replied_at
        "",        # vapi_assistant_id
        # Enrichment
        data.get("niche", ""),
        str(data.get("lead_score", "")),
        data.get("hiring_urgency", ""),
        data.get("pain_signal", ""),
        # Message tracking
        data.get("message_variant_id", ""),
        data.get("channel_used", ""),
        # Reply handling
        data.get("reply_status", ""),
        data.get("conversation_stage", "initial"),
        "",        # objection_type
        "0",       # follow_up_count
        # Outcomes
        "no",      # booked_call
        "no",      # closed_client
        # Rotation + threading + unsubscribe
        "",        # message_id — set after send
        data.get("opt_out_token", ""),  # opt_out_token — generated before save
        "",        # sender_account — set after send
        "no",      # opted_out
        # Person-level personalization hooks
        data.get("person_hook", ""),
        data.get("company_hook", ""),
        # WhatsApp sequence tracking
        data.get("phone", ""),
        "",   # whatsapp_stage
        "",   # whatsapp_reply_at
        # ── v3 digital maturity signals ───────────────────────────────────
        data.get("pms_signal", ""),
        "yes" if data.get("whatsapp_presence") else "no",
        "yes" if data.get("instagram_presence") else "no",
        "yes" if data.get("online_booking") else "no",
        "yes" if data.get("multi_location") else "no",
        data.get("language_signal", ""),
        data.get("review_velocity", ""),
        data.get("budget_proxy", ""),
        # ── v3 dual scoring ──────────────────────────────────────────────
        str(data.get("digital_maturity_score", "")),
        str(data.get("adoption_score", "")),
        str(data.get("pain_score", "")),
        # ── v3 Instagram channel ─────────────────────────────────────────
        data.get("instagram_handle", ""),
        data.get("instagram_msg", ""),
        "FALSE",  # instagram_sent
        # ── v3 per-channel follow-up counters ────────────────────────────
        "0",  # email_follow_up_count
        "0",  # whatsapp_follow_up_count
        "0",  # instagram_follow_up_count
        # ── v3 close attribution + LTV ───────────────────────────────────
        "",  # monthly_value
        "",  # cancelled_at
        "",  # churn_reason
        "",  # ltv
        "",  # deployment_clinic_id
        # ── 2026-05-23 inbound integration (personal-brand → sales pipeline) ──
        data.get("inbound_source", ""),
        data.get("attribution_post_id", ""),
        # ── 2026-05-29 local-business harvest (Maps source) ──
        data.get("icebreaker", ""),
        str(data.get("rating", "")),
        str(data.get("review_count", "")),
        data.get("maps_url", ""),
        # ── 2026-05-30 merged source + hot hiring flag ──
        data.get("source_type", ""),
        data.get("hiring_now", ""),
    ]


def save(data: dict, existing: list[dict] | None = None) -> bool:
    if existing is None:
        existing = get_all_leads()

    if domain_exists(data.get("domain"), existing):
        return False

    sheet = get_sheet("leads")
    row = build_row(data)
    # Ensure all elements are strings to prevent 400 APIError with structured values
    row = [str(val) if val is not None else "" for val in row]
    sheet.append_row(row)
    return True


def update_field(slug: str, field: str, value: str):
    sheet = get_sheet("leads")
    values = sheet.get_all_values()
    if not values:
        raise ValueError("Sheet is empty")
        
    # Find slug in the first column (slug)
    for i, row in enumerate(values):
        if i == 0: continue # Skip header
        if row and row[0] == slug:
            col = HEADERS.index(field) + 1
            sheet.update_cell(i + 1, col, value)
            return
    raise ValueError(f"Slug not found: {slug}")


def get_by_slug(slug: str) -> dict | None:
    records = get_all_leads()
    for row in records:
        if row.get("slug") == slug:
            return row
    return None


def update_reply(slug: str, reply_status: str, conversation_stage: str,
                 objection_type: str = ""):
    """Update reply tracking fields after a reply is received and classified."""
    update_field(slug, "reply_status", reply_status)
    update_field(slug, "conversation_stage", conversation_stage)
    update_field(slug, "replied_at", datetime.now(timezone.utc).isoformat())
    if objection_type:
        update_field(slug, "objection_type", objection_type)


def increment_follow_up(slug: str):
    """Increment follow_up_count by 1."""
    sheet = get_sheet("leads")
    values = sheet.get_all_values()
    if not values:
        return
    col_idx = HEADERS.index("follow_up_count") + 1
    for i, row in enumerate(values):
        if i == 0:
            continue
        if row and row[0] == slug:
            current = int(row[col_idx - 1] or "0")
            sheet.update_cell(i + 1, col_idx, str(current + 1))
            return


def update_channel(slug: str, channel: str):
    """Record which channel was used for first contact (email/linkedin)."""
    update_field(slug, "channel_used", channel)


def mark_booked(slug: str):
    update_field(slug, "booked_call", "yes")
    update_field(slug, "conversation_stage", "booked")


def mark_closed(slug: str):
    update_field(slug, "closed_client", "yes")
    update_field(slug, "conversation_stage", "closed")


# ── v3 helpers ─────────────────────────────────────────────────────────────

def mark_deployed(slug: str, clinic_id: str, monthly_value_inr: int):
    """Record a paid deployment. Links lead row to a deployment telemetry row."""
    update_field(slug, "deployment_clinic_id", clinic_id)
    update_field(slug, "monthly_value", str(monthly_value_inr))
    update_field(slug, "status", "deployed")
    update_field(slug, "closed_client", "yes")


def mark_churned(slug: str, reason: str):
    """Record churn. Computes final LTV from current monthly_value × months active."""
    sheet = get_sheet("leads")
    values = sheet.get_all_values()
    if not values:
        return
    deployed_at_idx = HEADERS.index("sent_at") + 1  # use sent_at as deployment proxy
    monthly_idx = HEADERS.index("monthly_value")
    cancelled_idx = HEADERS.index("cancelled_at") + 1
    churn_idx = HEADERS.index("churn_reason") + 1
    ltv_idx = HEADERS.index("ltv") + 1
    for i, row in enumerate(values):
        if i == 0 or not row or row[0] != slug:
            continue
        try:
            monthly = int(row[monthly_idx] or "0")
        except (ValueError, TypeError):
            monthly = 0
        # Crude LTV: months active × monthly. Refine when deployment_telemetry exists.
        try:
            from datetime import datetime as _dt
            deployed_at = _dt.fromisoformat((row[deployed_at_idx - 1] or "").replace("Z", "+00:00"))
            months = max(1, (datetime.now(timezone.utc) - deployed_at).days // 30)
        except Exception:
            months = 1
        ltv = monthly * months
        sheet.update_cell(i + 1, cancelled_idx, datetime.now(timezone.utc).isoformat())
        sheet.update_cell(i + 1, churn_idx, reason)
        sheet.update_cell(i + 1, ltv_idx, str(ltv))
        return


def increment_channel_followup(slug: str, channel: str):
    """Increment the per-channel follow-up counter. channel ∈ {email, whatsapp, instagram}."""
    field = f"{channel}_follow_up_count"
    if field not in HEADERS:
        raise ValueError(f"Unknown channel for follow-up: {channel}")
    sheet = get_sheet("leads")
    values = sheet.get_all_values()
    if not values:
        return
    col_idx = HEADERS.index(field) + 1
    for i, row in enumerate(values):
        if i == 0 or not row or row[0] != slug:
            continue
        current = 0
        try:
            current = int(row[col_idx - 1] or "0")
        except (ValueError, TypeError):
            pass
        sheet.update_cell(i + 1, col_idx, str(current + 1))
        # Keep legacy follow_up_count in sync so existing code paths still work
        legacy_idx = HEADERS.index("follow_up_count") + 1
        try:
            legacy = int(row[legacy_idx - 1] or "0")
        except (ValueError, TypeError):
            legacy = 0
        sheet.update_cell(i + 1, legacy_idx, str(legacy + 1))
        return


def upsert_niche_analytics(rows: list[dict]):
    """
    Write niche performance data to the 'niche_analytics' tab.
    Creates or overwrites all rows (full refresh, not append).
    """
    sheet = get_sheet("niche_analytics")
    # Ensure header row exists
    existing = sheet.get_all_values()
    if not existing or existing[0] != NICHE_ANALYTICS_HEADERS:
        sheet.clear()
        sheet.append_row(NICHE_ANALYTICS_HEADERS)

    # Write one row per niche (sorted by booked_call_rate desc)
    data_rows = [
        [
            r.get("niche", ""),
            str(r.get("total_sent", 0)),
            str(r.get("total_replies", 0)),
            str(r.get("total_booked_calls", 0)),
            str(r.get("total_clients", 0)),
            f"{r.get('reply_rate', 0):.1%}",
            f"{r.get('booked_call_rate', 0):.1%}",
            f"{r.get('conversion_rate', 0):.1%}",
        ]
        for r in rows
    ]
    # Clear existing data rows (keep header) then re-append
    current = sheet.get_all_values()
    if len(current) > 1:
        sheet.delete_rows(2, len(current))
    for row in data_rows:
        sheet.append_row(row)


def log_error(company_name: str, error: str):
    from .security_utils import redact_text
    error = redact_text(error)
    try:
        sheet = get_sheet("errors")
        sheet.append_row([
            company_name,
            error,
            datetime.now(timezone.utc).isoformat(),
        ])
    except Exception as e:
        log.error("Failed to log error to sheet for %s: %s (original error: %s)",
                  company_name, redact_text(str(e)), error)
