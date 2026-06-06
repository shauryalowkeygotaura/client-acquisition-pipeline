"""
modules/sender_warmup.py — v3 domain reputation protection

Cold Gmail accounts get throttled around 50–80 sends/day before inbox placement
degrades. A new account thrown straight to MAX_PER_ACCOUNT (50/day) burns its
reputation in the first week.

This module tracks per-account days_active and returns a ramp-adjusted cap:

  Week 1 (days 0–6):    10/day
  Week 2 (days 7–13):   25/day
  Week 3+ (days 14+):   50/day (= MAX_PER_ACCOUNT)

State persists in a `sender_warmup` tab on the Google Sheet so it survives
GitHub Actions runs (which restart from scratch each cron tick).

Integration: email_sender.py calls daily_cap_for(address) before its own
MAX_PER_ACCOUNT check, and takes the min of the two caps. Existing code keeps
working unchanged when the warmup tab is empty (returns MAX_PER_ACCOUNT).
"""
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

WARMUP_TAB = "sender_warmup"
WARMUP_HEADERS = ["address", "first_send_date", "total_sends"]

# Ramp schedule. Tunable via env so we can extend the warmup window for new
# accounts in lower-trust domains.
RAMP_WEEK_1 = int(os.getenv("WARMUP_WEEK_1_CAP", "10"))
RAMP_WEEK_2 = int(os.getenv("WARMUP_WEEK_2_CAP", "25"))
RAMP_WEEK_3_PLUS = int(os.getenv("WARMUP_FULL_CAP", "50"))

_state_cache: dict[str, dict] | None = None


def _load_state() -> dict[str, dict]:
    """Load warmup state from the Sheet tab. Cache for the rest of the process."""
    global _state_cache
    if _state_cache is not None:
        return _state_cache

    from modules import sheets_writer  # local import — avoid circular at module load
    try:
        sheet = sheets_writer.get_sheet(WARMUP_TAB)
    except Exception as e:
        log.info("sender_warmup tab not accessible (%s) — assuming all accounts warmed", e)
        _state_cache = {}
        return _state_cache

    try:
        values = sheet.get_all_values()
    except Exception as e:
        log.warning("Could not read warmup state: %s", e)
        _state_cache = {}
        return _state_cache

    state: dict[str, dict] = {}
    for row in values[1:]:  # skip header
        if not row or not row[0]:
            continue
        addr = row[0].strip().lower()
        first = row[1] if len(row) > 1 else ""
        sends = row[2] if len(row) > 2 else "0"
        try:
            total = int(sends or "0")
        except ValueError:
            total = 0
        state[addr] = {"first_send_date": first, "total_sends": total}
    _state_cache = state
    return state


def _days_active(first_send_iso: str) -> int:
    if not first_send_iso:
        return 0
    try:
        first = datetime.fromisoformat(first_send_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, (datetime.now(timezone.utc) - first).days)


def daily_cap_for(address: str) -> int:
    """Ramp-adjusted daily cap for this account.

    Untracked accounts default to full cap (RAMP_WEEK_3_PLUS) so existing
    accounts that pre-date warmup tracking keep their existing behavior.
    To force warmup for a known account, add it manually to the sheet tab.
    """
    state = _load_state()
    entry = state.get((address or "").strip().lower())
    if not entry or not entry.get("first_send_date"):
        return RAMP_WEEK_3_PLUS

    days = _days_active(entry["first_send_date"])
    if days < 7:
        return RAMP_WEEK_1
    if days < 14:
        return RAMP_WEEK_2
    return RAMP_WEEK_3_PLUS


def is_warmed(address: str) -> bool:
    """True once the account has ≥14 days of send history."""
    state = _load_state()
    entry = state.get((address or "").strip().lower())
    if not entry or not entry.get("first_send_date"):
        return True  # untracked = assumed pre-warmed (legacy accounts)
    return _days_active(entry["first_send_date"]) >= 14


def record_send(address: str) -> None:
    """
    Record that `address` just sent an email. Sets first_send_date on first
    contact, increments total_sends. Best-effort — failures here must never
    block a real send.
    """
    addr = (address or "").strip().lower()
    if not addr:
        return

    from modules import sheets_writer
    try:
        sheet = sheets_writer.get_sheet(WARMUP_TAB)
    except Exception:
        # Tab missing — try to create it lazily so the next call sees it.
        try:
            from modules.sheets_writer import get_sheet as _gs
            # Attempt to add the tab via the spreadsheet client
            ws = _gs("leads").spreadsheet  # type: ignore[attr-defined]
            ws.add_worksheet(title=WARMUP_TAB, rows=200, cols=5)
            new_tab = sheets_writer.get_sheet(WARMUP_TAB)
            new_tab.append_row(WARMUP_HEADERS)
            sheet = new_tab
        except Exception as e:
            log.debug("Could not auto-create sender_warmup tab: %s", e)
            return

    state = _load_state()
    entry = state.get(addr, {"first_send_date": "", "total_sends": 0})
    if not entry.get("first_send_date"):
        entry["first_send_date"] = datetime.now(timezone.utc).isoformat()
    entry["total_sends"] = (entry.get("total_sends") or 0) + 1
    state[addr] = entry

    # Persist: find row by address, update; if missing, append
    try:
        values = sheet.get_all_values()
        for i, row in enumerate(values):
            if i == 0:
                continue
            if row and row[0].strip().lower() == addr:
                sheet.update_cell(i + 1, 2, entry["first_send_date"])
                sheet.update_cell(i + 1, 3, str(entry["total_sends"]))
                return
        sheet.append_row([addr, entry["first_send_date"], str(entry["total_sends"])])
    except Exception as e:
        log.warning("Could not persist warmup state for %s: %s", addr, e)
