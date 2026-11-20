"""After-hours call check: call clinics after closing, record what happens.

Why: the strongest first line a cold email can have is a true one nobody else
can send. "I called Citadel Dental at 8:52pm on Tuesday. It rang out." beats
any "~5-10 missed calls a week" estimate, because it is specific and checkable.

How it works (your own Android phone over USB, $0):
  plan   picks leads waiting for a check (status=awaiting_call_check) and gives
         each a RANDOM time inside tonight's window, at least 6 min apart, so
         the calls don't look like a dialer working a list.
  run    waits for each slot, then dials. Afterwards it reads the phone's call
         log: duration 0 = nobody picked up. It writes one true sentence to the
         lead's call_check cell, and the next CI run emails the lead with it.

Dial modes:
  default ("tap")  opens the dialer with the number filled in and beeps; YOU
                   press call. That is human dialling under TRAI's rules.
  --auto           places the call itself and hangs up after --ring-seconds.
                   TRAI's 18-Sep-2026 amendment treats calls "made by software
                   without direct human dialling" as automated calls that must
                   be pre-declared to your carrier, and 3 complaints in 10 days
                   can get a number actioned. Use only if you accept that risk.

Usage (from the repo root, phone plugged in with USB debugging on):
  doppler run -- python scripts/after_hours_check.py plan --n 5
  doppler run -- python scripts/after_hours_check.py run
  python scripts/after_hours_check.py status

State is checkpointed in runs/call_plan.local.json after every slot, so a
crash or Ctrl+C resumes where it stopped.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = ZoneInfo("Asia/Kolkata")
PLAN_FILE = ROOT / "runs" / "call_plan.local.json"
AWAITING = "awaiting_call_check"
MIN_GAP_MIN = 6
LOG_WAIT_SECONDS = 240  # tap mode: how long to wait for the call to happen + end
_PHONE_OK = re.compile(r"^\+91[6-9]\d{9}$|^\+91[1-5]\d{9}$")


# ── phone numbers: whitelist, never sanitise ────────────────────────────────
def normalise_phone(raw: str) -> str | None:
    """Return +91XXXXXXXXXX or None. The result goes into an adb shell string,
    so only a fully known-good shape is ever allowed through."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[1:]
    candidate = "+" + digits
    return candidate if _PHONE_OK.match(candidate) else None


# ── adb ─────────────────────────────────────────────────────────────────────
def preflight() -> None:
    """Fail loudly, with the fix, before anything is scheduled."""
    if not shutil.which("adb"):
        sys.exit(
            "adb not found.\n"
            "Fix: install Google's Android platform-tools\n"
            "  winget install Google.PlatformTools\n"
            "then reopen the terminal so adb is on PATH."
        )
    out = _adb(["devices"], use_shell=False)
    devices = [l for l in out.splitlines()[1:] if l.strip().endswith("device")]
    if not devices:
        sys.exit(
            "No phone found by adb.\n"
            "Fix: plug the phone in by USB, turn on Developer options > USB debugging,\n"
            "and accept the 'Allow USB debugging' prompt on the phone. Then run `adb devices`."
        )
    probe = _adb(["shell", "content query --uri content://call_log/calls --projection date --sort 'date DESC' --where 'type=2'"])
    if "Error" in probe or "Permission" in probe:
        sys.exit(f"Phone call log is not readable over adb:\n{probe.strip()}")


def _adb(args: list[str], use_shell: bool = False, timeout: int = 30) -> str:
    try:
        r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "Error: adb timed out"
    return (r.stdout or "") + (r.stderr or "")


def dial(phone: str, auto: bool) -> None:
    action = "android.intent.action.CALL" if auto else "android.intent.action.DIAL"
    _adb(["shell", f"am start -a {action} -d tel:{phone}"])


def hang_up() -> None:
    _adb(["shell", "input keyevent KEYCODE_ENDCALL"])


_ROW = re.compile(r"number=([^,]*),\s*date=(\d+),\s*duration=(\d+),\s*type=(\d+)")


def last_outgoing(phone: str, since_ms: int) -> tuple[int, int] | None:
    """(date_ms, duration_s) of the newest outgoing call to `phone` after since_ms."""
    out = _adb(["shell",
                "content query --uri content://call_log/calls "
                "--projection number:date:duration:type --sort 'date DESC'"])
    tail = phone[-10:]
    for m in _ROW.finditer(out):
        number, date_ms, duration, ctype = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if ctype != "2" or date_ms < since_ms:
            continue
        if re.sub(r"\D", "", number)[-10:] == tail:
            return date_ms, duration
    return None


# ── plan state ──────────────────────────────────────────────────────────────
def load_plan() -> dict:
    try:
        return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"slots": []}


def save_plan(plan: dict) -> None:
    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PLAN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    tmp.replace(PLAN_FILE)


def random_times(n: int, start: datetime, end: datetime) -> list[datetime]:
    """n random times in [start, end], sorted, at least MIN_GAP_MIN apart."""
    span = int((end - start).total_seconds())
    need = (n - 1) * MIN_GAP_MIN * 60
    if span <= need:
        raise SystemExit(f"Window too small for {n} calls {MIN_GAP_MIN} min apart.")
    # Spread n points over (span - need) of free time, then add the gaps back.
    free = sorted(random.uniform(0, span - need) for _ in range(n))
    return [start + timedelta(seconds=int(f + i * MIN_GAP_MIN * 60 + random.randint(0, 50)))
            for i, f in enumerate(free)]


def parse_window(text: str, day: datetime) -> tuple[datetime, datetime]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", text.strip())
    if not m:
        raise SystemExit("--window must look like 20:40-22:10")
    h1, m1, h2, m2 = map(int, m.groups())
    start = day.replace(hour=h1, minute=m1, second=0, microsecond=0)
    end = day.replace(hour=h2, minute=m2, second=0, microsecond=0)
    if end <= start:
        raise SystemExit("--window end must be after start")
    return start, end


# ── commands ────────────────────────────────────────────────────────────────
def cmd_plan(args) -> None:
    from modules import sheets_writer

    leads = sheets_writer.get_all_leads()
    candidates = []
    for lead in leads:
        if lead.get("status") != AWAITING or lead.get("call_check_at"):
            continue
        phone = normalise_phone(lead.get("phone", ""))
        if phone and lead.get("slug"):
            candidates.append({"slug": lead["slug"], "company": lead.get("company_name", ""),
                               "phone": phone})
    if not candidates:
        print("No leads waiting for a call check. (Is CALL_CHECK_FIRST=1 set on the pipeline?)")
        return
    random.shuffle(candidates)
    picks = candidates[: args.n]

    now = datetime.now(IST)
    start, end = parse_window(args.window, now)
    if end <= now + timedelta(minutes=5):  # tonight's window has passed: use tomorrow's
        start, end = start + timedelta(days=1), end + timedelta(days=1)
    start = max(start, now + timedelta(minutes=2))
    times = random_times(len(picks), start, end)

    plan = {"created": now.isoformat(), "auto": args.auto, "slots": []}
    for lead, t in zip(picks, times):
        plan["slots"].append({**lead, "at": t.isoformat(), "state": "planned"})
    save_plan(plan)
    print(f"Planned {len(picks)} calls ({'AUTO dial' if args.auto else 'you tap call'}):")
    for s in plan["slots"]:
        print(f"  {datetime.fromisoformat(s['at']).strftime('%a %H:%M:%S')}  {s['company']}")
    print("Now run:  doppler run -- python scripts/after_hours_check.py run")


def _sentence(call_ms: int) -> str:
    t = datetime.fromtimestamp(call_ms / 1000, tz=IST)
    hour = t.strftime("%I").lstrip("0")
    return f"{t.strftime('%A')} {hour}:{t.strftime('%M%p').lower()}: called the clinic number, it rang out and nobody answered."


def cmd_run(args) -> None:
    from modules import sheets_writer

    preflight()
    plan = load_plan()
    auto = plan.get("auto", False)
    todo = [s for s in plan["slots"] if s["state"] == "planned"]
    if not todo:
        print("Nothing planned. Run `plan` first.")
        return
    print(f"{len(todo)} calls left. Keep this window open and the phone plugged in.")
    for slot in todo:
        at = datetime.fromisoformat(slot["at"])
        wait = (at - datetime.now(IST)).total_seconds()
        if wait < -15 * 60:
            slot["state"] = "missed"  # machine was asleep; leave the lead for another night
            save_plan(plan)
            print(f"  missed slot for {slot['company']} (more than 15 min late), skipping")
            continue
        if wait > 0:
            print(f"  next: {slot['company']} at {at.strftime('%H:%M:%S')} (in {int(wait // 60)} min)")
            time.sleep(wait)

        since_ms = int(time.time() * 1000) - 5_000
        dial(slot["phone"], auto)
        if auto:
            time.sleep(args.ring_seconds)
            hang_up()
            time.sleep(4)
        else:
            print(f"\a  >>> TAP CALL on the phone now: {slot['company']}. "
                  f"Let it ring ~{args.ring_seconds}s, then hang up. If someone answers, "
                  f"be honest: you're a student checking clinic phone hours, sorry to disturb.")

        found = None
        deadline = time.time() + (10 if auto else LOG_WAIT_SECONDS)
        while time.time() < deadline and not found:
            found = last_outgoing(slot["phone"], since_ms)
            # Tap mode: only accept the entry once the call has ended (duration is
            # written at hang-up), i.e. it appears in the log at all.
            if not found:
                time.sleep(5)
        if not found:
            slot["state"] = "no_call"
            save_plan(plan)
            print(f"  no call logged for {slot['company']}; left for another night")
            continue

        call_ms, duration = found
        called_at = datetime.fromtimestamp(call_ms / 1000, tz=timezone.utc).isoformat()
        if duration == 0:
            text = _sentence(call_ms)
            slot["state"] = "rang_out"
        else:
            text = "ANSWERED"
            slot["state"] = "answered"
        try:
            sheets_writer.update_field(slot["slug"], "call_check", text)
            sheets_writer.update_field(slot["slug"], "call_check_at", called_at)
        except Exception as e:
            slot["state"] = "sheet_error"
            slot["error"] = str(e)[:200]
            print(f"  could not write to the sheet for {slot['company']}: {e}")
        save_plan(plan)
        print(f"  {slot['company']}: {slot['state']} ({duration}s)")
    print("Done. The next pipeline run emails these leads with their call-check line.")


def cmd_status(_args) -> None:
    plan = load_plan()
    if not plan["slots"]:
        print("No plan.")
        return
    for s in plan["slots"]:
        print(f"{datetime.fromisoformat(s['at']).strftime('%a %H:%M')}  {s['state']:<11} {s['company']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--n", type=int, default=5, help="calls tonight (default 5)")
    p.add_argument("--window", default="20:40-22:10", help="IST window, default 20:40-22:10")
    p.add_argument("--auto", action="store_true", help="dial without a tap (see TRAI note)")
    r = sub.add_parser("run")
    r.add_argument("--ring-seconds", type=int, default=30)
    sub.add_parser("status")
    args = ap.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
