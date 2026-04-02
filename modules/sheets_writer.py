import json
import logging
import os
from datetime import datetime, timezone

import gspread
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
]

NICHE_ANALYTICS_HEADERS = [
    "niche", "total_sent", "total_replies", "total_booked_calls",
    "total_clients", "reply_rate", "booked_call_rate", "conversion_rate",
]


def get_sheet(tab: str = "leads"):
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set.")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEETS_ID env var is not set.")
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).worksheet(tab)


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
    return [
        # Original columns
        data.get("slug", ""),
        data.get("company_name", ""),
        data.get("company_website", ""),
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
        "pending", # status
        "",        # sent_at
        "",        # replied_at
        "",        # vapi_assistant_id
        # Enrichment
        data.get("niche", ""),
        str(data.get("lead_score", "")),
        data.get("hiring_urgency", ""),
        data.get("pain_signal", ""),
        # Message tracking
        data.get("message_variant_id", ""),
        "",        # channel_used — set by pipeline after send
        # Reply handling (empty at creation)
        "",        # reply_status
        "initial", # conversation_stage
        "",        # objection_type
        "0",       # follow_up_count
        # Outcomes
        "no",      # booked_call
        "no",      # closed_client
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
    try:
        sheet = get_sheet("errors")
        sheet.append_row([
            company_name,
            error,
            datetime.now(timezone.utc).isoformat(),
        ])
    except Exception as e:
        log.error("Failed to log error to sheet for %s: %s (original error: %s)", company_name, e, error)
