"""
scripts/pull_drills.py - sweep Grill Room drill reports into the vault.

The clinic-demo page's ?mode=drill (the Grill Room: Groq plays Dr. Sharma, a
hostile Jaipur dentist, and grades Shaurya's cold-call reps against
Projects/dental-receptionist/cold-call-playbook.md) produces one markdown
report per session. This script collects those reports from wherever they
landed and appends the NEW ones to the drill log that lives next to the
playbook they train against:

    C:/Users/shaur/OneDrive/Desktop/Vault/Projects/dental-receptionist/drill-log.md

Two sources, both optional, both checkpointed so reruns are idempotent:
1. GitHub ledger (the Demo Black Box repo, drills/ directory) via the free
   Contents API. Only tried when DRILL_LEDGER_REPO is set ("owner/repo").
   GITHUB_TOKEN (Doppler) is optional - a public repo works keyless.
2. Local Downloads sweep - when the ledger endpoint is not deployed the demo
   page degrades to a "Download .md" button, which drops
   grill-room-drill-*.md files into ~/Downloads.

State: runs/drill_pull_state.json (seen file keys -> first-seen timestamp).
$0 and offline-safe: no LLM calls, nothing is sent anywhere, a dead network
just means the ledger source is skipped and Downloads is still swept.

Run: python scripts/pull_drills.py
Env: DRILL_LEDGER_REPO   optional "owner/repo" of the Demo Black Box ledger
     GITHUB_TOKEN        optional, for a private ledger repo (via Doppler)
     DRILL_LOG_PATH      override the vault drill-log.md target
     DRILL_DOWNLOADS_DIR override the local sweep directory
"""
import base64
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "runs" / "drill_pull_state.json"

DRILL_LOG_PATH = Path(
    os.getenv(
        "DRILL_LOG_PATH",
        "C:/Users/shaur/OneDrive/Desktop/Vault/Projects/dental-receptionist/drill-log.md",
    )
)
DOWNLOADS_DIR = Path(os.getenv("DRILL_DOWNLOADS_DIR", str(Path.home() / "Downloads")))
LEDGER_REPO = os.getenv("DRILL_LEDGER_REPO", "").strip()
LEDGER_DIR = "drills"
TIMEOUT = 20

DRILL_LOG_HEADER = """# Grill Room drill log

Auto-appended by `Code/client-acquisition-pipeline/scripts/pull_drills.py`.
Each entry is one Grill Room session (`?mode=drill` on the clinic demo),
scored against [[cold-call-playbook]]. Do not edit entries by hand; add new
objections you hear in the field to the playbook instead - the simulator
picks them up from there.

#sales #drill #dental-receptionist
"""


def load_state():
    """Return {"seen": {key: iso timestamp}} - tolerate a missing/corrupt file."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if isinstance(state, dict) and isinstance(state.get("seen"), dict):
            return state
    except (OSError, ValueError):
        pass
    return {"seen": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, indent=2)


def fetch_ledger_reports():
    """Yield (key, name, markdown) from the GitHub ledger's drills/ dir.

    Every HTTP status is checked and every JSON parse is guarded; any failure
    logs a one-liner and yields nothing (the Downloads sweep still runs).
    """
    if not LEDGER_REPO:
        print("ledger: DRILL_LEDGER_REPO not set - skipping (Downloads sweep only)")
        return
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{LEDGER_REPO}/contents/{LEDGER_DIR}"
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"ledger: unreachable ({e.__class__.__name__}) - skipping")
        return
    if r.status_code == 404:
        print(f"ledger: {LEDGER_REPO}/{LEDGER_DIR}/ not found (nothing filed yet?) - skipping")
        return
    if r.status_code != 200:
        print(f"ledger: GitHub returned {r.status_code} - skipping")
        return
    try:
        entries = r.json()
    except ValueError:
        print("ledger: contents API returned non-JSON - skipping")
        return
    if not isinstance(entries, list):
        print("ledger: unexpected contents payload - skipping")
        return

    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        name = entry.get("name", "")
        if not name.endswith(".md"):
            continue
        key = f"ledger:{name}@{entry.get('sha', '')}"
        # Small files come inline via the blob URL; download_url is simplest
        # and free either way.
        text = None
        dl = entry.get("download_url")
        if dl:
            try:
                fr = requests.get(dl, headers=headers, timeout=TIMEOUT)
                if fr.status_code == 200:
                    text = fr.text
                else:
                    print(f"ledger: {name}: download returned {fr.status_code} - skipped")
            except requests.RequestException as e:
                print(f"ledger: {name}: download failed ({e.__class__.__name__}) - skipped")
        elif isinstance(entry.get("content"), str):
            try:
                text = base64.b64decode(entry["content"]).decode("utf-8", "replace")
            except (ValueError, TypeError):
                print(f"ledger: {name}: undecodable inline content - skipped")
        if text and text.strip():
            yield key, name, text


def sweep_downloads():
    """Yield (key, name, markdown) for grill-room-drill-*.md in Downloads.

    Keyed by name + content hash so re-downloading the same report (browser
    "(1)" suffixes aside) never double-appends.
    """
    if not DOWNLOADS_DIR.is_dir():
        print(f"downloads: {DOWNLOADS_DIR} not found - skipping")
        return
    for path in sorted(DOWNLOADS_DIR.glob("grill-room-drill-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"downloads: {path.name}: unreadable ({e.__class__.__name__}) - skipped")
            continue
        if not text.strip():
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        yield f"local:{digest}", path.name, text


def append_reports(reports):
    """Append reports to drill-log.md (created with a header on first run)."""
    DRILL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not DRILL_LOG_PATH.exists()
    with open(DRILL_LOG_PATH, "a", encoding="utf-8", newline="\n") as f:
        if is_new:
            f.write(DRILL_LOG_HEADER)
        for _key, name, text in reports:
            f.write(f"\n---\n\n<!-- source: {name} · pulled {datetime.now():%Y-%m-%d %H:%M} -->\n\n")
            f.write(text.rstrip() + "\n")
    return is_new


def main():
    state = load_state()
    seen = state["seen"]

    new_reports = []
    for source in (fetch_ledger_reports(), sweep_downloads()):
        for key, name, text in source:
            if key in seen:
                continue
            new_reports.append((key, name, text))

    if not new_reports:
        print(f"nothing new. drill log: {DRILL_LOG_PATH}")
        return 0

    # Chronological-ish: the report filenames embed YYYY-MM-DD-HHMM.
    new_reports.sort(key=lambda r: r[1])
    append_reports(new_reports)
    now = datetime.now().isoformat(timespec="seconds")
    for key, name, _text in new_reports:
        seen[key] = now
        print(f"appended: {name}")
    save_state(state)
    print(f"{len(new_reports)} new report(s) -> {DRILL_LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
