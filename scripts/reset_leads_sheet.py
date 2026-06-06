"""
scripts/reset_leads_sheet.py — Back up + wipe the 'leads' tab.

Safe reset: dumps every current row to a timestamped JSON file under
backups/ FIRST, then clears the leads tab while preserving the header row.
Does NOT touch the errors / niche_analytics tabs.

Run via Doppler so the Google Sheets creds inject:
    doppler run --project client-acquisition-pipeline --config dev -- \
        python scripts/reset_leads_sheet.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import sheets_writer  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def main() -> None:
    sheet = sheets_writer.get_sheet("leads")
    values = sheet.get_all_values()
    row_count = max(0, len(values) - 1)  # minus header

    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    backup_path = BACKUP_DIR / f"leads-backup-{stamp}.json"
    backup_path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[BACKUP] {row_count} data row(s) -> {backup_path}")

    if not values:
        # Empty sheet: lay down our canonical header so future appends align.
        sheet.append_row(sheets_writer.HEADERS)
        print("[RESET] Sheet was empty. Wrote canonical header row.")
        return

    header = values[0]
    # Clear everything, then restore the header row only.
    sheet.clear()
    sheet.append_row(header)
    print(f"[RESET] Cleared {row_count} data row(s). Header preserved "
          f"({len(header)} columns).")


if __name__ == "__main__":
    main()
