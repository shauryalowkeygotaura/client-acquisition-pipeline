"""
modules/deployment_telemetry.py — v3 ROI feedback loop (stubbed)

Closes the loop between outbound and operational reality.

Until now the pipeline ends at `booked_call=yes`. That tells us a sales motion
worked, not that a deployment worked. This module records the post-sale truth
for every deployed clinic:

  calls_handled            — answered by the AI
  calls_missed_pre_ai      — baseline before deployment (owner-reported or audit)
  bookings_recovered       — new patient appointments captured by the AI
  revenue_recovered_inr    — bookings_recovered × average ticket size
  churn_at, churn_reason   — if/when the clinic cancels

Eventually the scorer reads aggregate "revenue_recovered per clinic per niche"
back as a scoring signal. That replaces the regex-only heuristics with the
actual outcome distribution.

CURRENT STATUS — stub:
  - Sheet schema defined (creates `deployments` tab on first use).
  - register_deployment() works (writes a row, links to lead via clinic_id).
  - record_call() works (appends to a per-clinic in-memory log; persists on flush).
  - daily_telemetry_sync() is a TODO — needs Twilio Logs API or VAPI webhook handler.

To activate the real ingestion: wire VAPI's `end-of-call-report` webhook to
hit a sync endpoint that calls record_call() for each call.
"""
import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DEPLOYMENTS_TAB = "deployments"
DEPLOYMENTS_HEADERS = [
    "clinic_id", "lead_slug", "clinic_name", "city", "niche",
    "deployed_at", "vapi_assistant_id",
    "calls_handled", "calls_missed_pre_ai", "bookings_recovered",
    "average_ticket_inr", "revenue_recovered_inr",
    "monthly_value_inr", "active", "churn_at", "churn_reason",
    "last_sync_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_or_create_tab():
    """Return the deployments sheet tab. Create it on first use."""
    from modules import sheets_writer
    try:
        return sheets_writer.get_sheet(DEPLOYMENTS_TAB)
    except Exception:
        pass
    # Lazily create the tab
    try:
        leads_sheet = sheets_writer.get_sheet("leads")
        spreadsheet = leads_sheet.spreadsheet  # type: ignore[attr-defined]
        new_tab = spreadsheet.add_worksheet(title=DEPLOYMENTS_TAB, rows=500, cols=20)
        new_tab.append_row(DEPLOYMENTS_HEADERS)
        return new_tab
    except Exception as e:
        log.error("Failed to create deployments tab: %s", e)
        raise


def register_deployment(
    lead_slug: str,
    clinic_name: str,
    city: str,
    niche: str,
    vapi_assistant_id: str,
    monthly_value_inr: int,
    average_ticket_inr: int = 2000,
    calls_missed_pre_ai: int = 0,
) -> str:
    """
    Register a paid deployment. Returns a new clinic_id (uuid4 hex prefix).

    Also calls sheets_writer.mark_deployed on the originating lead row so the
    sales pipeline and the deployment pipeline are linked by clinic_id.
    """
    from modules import sheets_writer
    tab = _get_or_create_tab()
    clinic_id = uuid.uuid4().hex[:12]

    tab.append_row([
        clinic_id, lead_slug, clinic_name, city, niche,
        _now(), vapi_assistant_id,
        "0",                                  # calls_handled
        str(calls_missed_pre_ai),
        "0",                                  # bookings_recovered
        str(average_ticket_inr),
        "0",                                  # revenue_recovered_inr
        str(monthly_value_inr),
        "yes",                                # active
        "",                                   # churn_at
        "",                                   # churn_reason
        _now(),                               # last_sync_at
    ])

    # Link back to the lead row
    try:
        sheets_writer.mark_deployed(lead_slug, clinic_id, monthly_value_inr)
    except Exception as e:
        log.warning("Could not link lead %s to deployment %s: %s", lead_slug, clinic_id, e)

    log.info("Registered deployment %s for %s (monthly ₹%d)", clinic_id, clinic_name, monthly_value_inr)
    return clinic_id


def record_call(
    clinic_id: str,
    handled: bool = True,
    booking_made: bool = False,
) -> None:
    """
    Record a single call event for a deployed clinic.

    Called by the VAPI webhook handler on `end-of-call-report`. Increments
    calls_handled (if AI answered) and bookings_recovered (if the call ended
    with a confirmed appointment).

    Stub note: this writes one cell at a time. At scale, batch updates by
    clinic_id and flush per cron tick.
    """
    tab = _get_or_create_tab()
    values = tab.get_all_values()
    if not values:
        log.warning("deployments tab empty — cannot record call for %s", clinic_id)
        return

    headers = values[0]
    try:
        id_idx = headers.index("clinic_id")
        handled_idx = headers.index("calls_handled") + 1
        bookings_idx = headers.index("bookings_recovered") + 1
        revenue_idx = headers.index("revenue_recovered_inr") + 1
        ticket_idx = headers.index("average_ticket_inr")
        last_sync_idx = headers.index("last_sync_at") + 1
    except ValueError as e:
        log.error("deployments tab schema mismatch: %s", e)
        return

    for i, row in enumerate(values):
        if i == 0 or len(row) <= id_idx:
            continue
        if row[id_idx] != clinic_id:
            continue

        try:
            calls = int(row[handled_idx - 1] or "0")
            bookings = int(row[bookings_idx - 1] or "0")
            revenue = int(row[revenue_idx - 1] or "0")
            ticket = int(row[ticket_idx] or "0")
        except (ValueError, TypeError):
            calls = bookings = revenue = ticket = 0

        if handled:
            calls += 1
        if booking_made:
            bookings += 1
            revenue += ticket

        tab.update_cell(i + 1, handled_idx, str(calls))
        tab.update_cell(i + 1, bookings_idx, str(bookings))
        tab.update_cell(i + 1, revenue_idx, str(revenue))
        tab.update_cell(i + 1, last_sync_idx, _now())
        return

    log.warning("No deployment row found for clinic_id=%s", clinic_id)


def record_churn(clinic_id: str, reason: str) -> None:
    """Mark a deployment as churned. Also updates the linked lead row's LTV."""
    from modules import sheets_writer
    tab = _get_or_create_tab()
    values = tab.get_all_values()
    if not values:
        return
    headers = values[0]
    try:
        id_idx = headers.index("clinic_id")
        active_idx = headers.index("active") + 1
        churn_at_idx = headers.index("churn_at") + 1
        churn_reason_idx = headers.index("churn_reason") + 1
        slug_idx = headers.index("lead_slug")
    except ValueError:
        return
    for i, row in enumerate(values):
        if i == 0 or len(row) <= id_idx or row[id_idx] != clinic_id:
            continue
        tab.update_cell(i + 1, active_idx, "no")
        tab.update_cell(i + 1, churn_at_idx, _now())
        tab.update_cell(i + 1, churn_reason_idx, reason)
        # Trigger LTV computation on the lead row
        try:
            sheets_writer.mark_churned(row[slug_idx], reason)
        except Exception as e:
            log.warning("Could not mark lead churned for %s: %s", row[slug_idx], e)
        return


def get_clinic_metrics(clinic_id: str) -> dict | None:
    """Read the current row for a clinic_id. Returns None if not found."""
    tab = _get_or_create_tab()
    values = tab.get_all_values()
    if not values:
        return None
    headers = values[0]
    try:
        id_idx = headers.index("clinic_id")
    except ValueError:
        return None
    for i, row in enumerate(values):
        if i == 0 or len(row) <= id_idx or row[id_idx] != clinic_id:
            continue
        return {h: (row[j] if j < len(row) else "") for j, h in enumerate(headers)}
    return None


def aggregate_by_niche() -> list[dict]:
    """
    Aggregate deployment outcomes by niche. Returns a list of dicts:
      niche, deployments_active, avg_monthly_value, avg_bookings_per_month,
      avg_revenue_recovered, churn_rate.

    Eventually consumed by the scorer to replace the static niche_bonus weights
    with observed outcome distributions.
    """
    tab = _get_or_create_tab()
    values = tab.get_all_values()
    if not values:
        return []
    headers = values[0]
    rows = values[1:]
    by_niche: dict[str, dict] = {}
    for row in rows:
        if len(row) <= headers.index("niche"):
            continue
        niche = row[headers.index("niche")] or "unknown"
        active = (row[headers.index("active")] or "no").lower() == "yes"
        try:
            monthly = int(row[headers.index("monthly_value_inr")] or "0")
            bookings = int(row[headers.index("bookings_recovered")] or "0")
            revenue = int(row[headers.index("revenue_recovered_inr")] or "0")
        except (ValueError, TypeError):
            monthly = bookings = revenue = 0

        bucket = by_niche.setdefault(niche, {
            "niche": niche, "total": 0, "active": 0,
            "sum_monthly": 0, "sum_bookings": 0, "sum_revenue": 0,
        })
        bucket["total"] += 1
        if active:
            bucket["active"] += 1
        bucket["sum_monthly"] += monthly
        bucket["sum_bookings"] += bookings
        bucket["sum_revenue"] += revenue

    out = []
    for n, b in by_niche.items():
        out.append({
            "niche": n,
            "deployments_active": b["active"],
            "deployments_total": b["total"],
            "avg_monthly_value_inr": (b["sum_monthly"] // b["total"]) if b["total"] else 0,
            "avg_bookings_per_clinic": (b["sum_bookings"] // b["total"]) if b["total"] else 0,
            "avg_revenue_recovered_inr": (b["sum_revenue"] // b["total"]) if b["total"] else 0,
            "churn_rate": ((b["total"] - b["active"]) / b["total"]) if b["total"] else 0.0,
        })
    return out


def daily_telemetry_sync() -> int:
    """
    TODO: pull yesterday's VAPI / Twilio call logs and call record_call() for each.

    Implementation plan:
      1. For each active clinic, read VAPI assistant_id from deployments tab.
      2. Call VAPI `/v1/call/list?assistant_id=...&start_date=yesterday` to get calls.
      3. For each call, infer booking_made from the structured outcome (or transcript).
      4. record_call(clinic_id, handled=True, booking_made=...).

    Currently a stub — returns 0 calls processed.
    """
    log.info("daily_telemetry_sync: not yet implemented (stub)")
    return 0
