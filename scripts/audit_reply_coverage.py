"""
scripts/audit_reply_coverage.py - reply-bot coverage matrix (feature d).

Introspects the REAL modules (no hardcoded yes/no table) and reports, for each
outreach channel, whether it has a classify -> draft -> send reply loop. This is
the audit that motivated reply_router: email had the full loop, WhatsApp/IG did
not. Run it after any reply change to see the gaps close.

Pure introspection: it imports the modules and checks for the capability
functions with getattr. No credentials, no network, no sends. Safe to run
anywhere via `python scripts/audit_reply_coverage.py`.

Capability is resolved by probing a small ordered list of (module, attr)
candidates per cell; the FIRST that exists wins and is shown as the provider.
"""
import importlib
import sys
from pathlib import Path

# Make the repo root importable when run as `python scripts/audit_reply_coverage.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# channel -> capability -> ordered candidate (module_path, attribute) probes.
# The matrix is derived by introspection, not asserted: change the code and the
# table changes with it.
MATRIX = {
    "email": {
        "classify": [("modules.reply_handler", "_classify_reply")],
        "draft": [("modules.reply_handler", "_generate_response")],
        "send": [("modules.reply_handler", "_send_reply"), ("modules.email_sender", "send")],
    },
    "whatsapp": {
        "classify": [("modules.social_brain", "classify")],
        "draft": [("modules.reply_router", "route_whatsapp"), ("modules.social_brain", "craft_reply")],
        "send": [("modules.whatsapp", "send_freeform"), ("modules.whatsapp", "send")],
    },
    "instagram": {
        "classify": [("modules.social_brain", "classify")],
        "draft": [("modules.reply_router", "route_instagram"), ("modules.social_brain", "craft_reply")],
        "send": [("modules.instagram", "send")],
    },
    "linkedin": {
        "classify": [("modules.social_brain", "classify")],
        "draft": [("modules.social_brain", "craft_reply")],
        "send": [("modules.linkedin", "send")],
    },
    # Phone is intentionally not auto-handled: the user is a minor and nothing
    # may auto-dial. We surface it so the gap is explicit, not hidden.
    "phone": {
        "classify": [],
        "draft": [],
        "send": [],
    },
}

CAPS = ["classify", "draft", "send"]


def _resolve(candidates):
    """Return (label, present) for the first existing (module, attr) candidate."""
    for mod_path, attr in candidates:
        try:
            mod = importlib.import_module(mod_path)
        except Exception as e:
            # Import failure is itself a coverage gap worth surfacing.
            return (f"{mod_path}: import error ({type(e).__name__})", False)
        if hasattr(mod, attr):
            short = mod_path.replace("modules.", "")
            return (f"{short}.{attr}", True)
    return ("-", False)


def build_report() -> dict:
    report = {}
    for channel, caps in MATRIX.items():
        report[channel] = {}
        for cap in CAPS:
            label, present = _resolve(caps.get(cap, []))
            report[channel][cap] = {"provider": label, "present": present}
    return report


def print_report(report: dict) -> None:
    mark = {True: "yes", False: " - "}
    name_w = max(len(c) for c in report) + 1
    header = f"{'channel'.ljust(name_w)}  {'classify':>8}  {'draft':>8}  {'send':>8}   providers"
    print(header)
    print("-" * len(header))
    for channel, caps in report.items():
        cells = "  ".join(f"{mark[caps[c]['present']]:>8}" for c in CAPS)
        provs = " | ".join(f"{c}:{caps[c]['provider']}" for c in CAPS)
        print(f"{channel.ljust(name_w)}  {cells}   {provs}")

    # Gap summary - which (channel, capability) cells are still empty.
    gaps = [
        f"{ch}.{cap}"
        for ch, caps in report.items()
        for cap in CAPS
        if not caps[cap]["present"]
    ]
    print("\nGaps:", ", ".join(gaps) if gaps else "none")
    full = [ch for ch, caps in report.items() if all(caps[c]["present"] for c in CAPS)]
    print("Full classify->draft->send loops:", ", ".join(full) if full else "none")


if __name__ == "__main__":
    print_report(build_report())
