"""Create/extend the private "people" tab from a CSV kept in the vault.

  doppler run -- python scripts/seed_people.py "<path to people-seed.csv>"

CSV columns: name,role,company,category,hook,profile,status
Rows whose name+company already exist in the tab are skipped (idempotent).
The CSV lives in the vault (private), never in this public repo.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import people_daily, sheets_writer  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    if not src.is_file():
        sys.exit(f"CSV not found: {src}")
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    try:
        ws = sheets_writer.get_sheet(people_daily.TAB)
    except Exception:
        sh = sheets_writer.get_sheet("leads").spreadsheet
        ws = sh.add_worksheet(title=people_daily.TAB, rows=500, cols=len(people_daily.HEADERS))
        ws.append_row(people_daily.HEADERS)
        print(f"created tab '{people_daily.TAB}'")
    if not ws.row_values(1):
        ws.append_row(people_daily.HEADERS)
    existing = {(r[0].strip().lower(), (r[2] if len(r) > 2 else "").strip().lower())
                for r in ws.get_all_values()[1:] if r}
    new = []
    for r in rows:
        key = ((r.get("name") or "").strip().lower(), (r.get("company") or "").strip().lower())
        if not key[0] or key in existing:
            continue
        if (r.get("category") or "") not in people_daily.CATEGORIES:
            print(f"  skip {r.get('name')}: category must be one of {people_daily.CATEGORIES}")
            continue
        new.append([r.get(h, "") or "" for h in people_daily.HEADERS])
        existing.add(key)
    if new:
        ws.append_rows(new)
    print(f"added {len(new)} people ({len(rows) - len(new)} skipped as duplicates/invalid)")


if __name__ == "__main__":
    main()
