import json
import os
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "slug", "company_name", "website", "domain", "contact_name",
    "email", "linkedin_url", "vapi_prompt", "email_subject",
    "email_body", "linkedin_msg", "email_sent", "linkedin_sent",
    "status", "sent_at", "replied_at", "vapi_assistant_id",
]


def get_sheet(tab: str = "leads"):
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
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
        "FALSE",
        "FALSE",
        "pending",
        "",
        "",
        "",
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


def log_error(company_name: str, error: str):
    try:
        sheet = get_sheet("errors")
        sheet.append_row([
            company_name,
            error,
            datetime.now(timezone.utc).isoformat(),
        ])
    except Exception:
        print(f"[ERROR LOG FAILED] {company_name}: {error}")
