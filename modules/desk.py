"""
modules/desk.py — data for the Command Center DESK (Harvey's dashboard,
rebuilt on this pipeline's own data, 2026-10-03).

Two files, because this repo is PUBLIC:

  runs/desk.json      counts only. Safe for anyone to read: funnel numbers,
                      14-day send/bounce/reply rates, pause state.
  runs/desk.enc.json  everything with a NAME in it (which clinic replied, who
                      is waiting on Shaurya, what got blocked), AES-256-GCM
                      encrypted. The key is derived (PBKDF2-SHA256) from
                      DESK_PASSPHRASE, which lives in Doppler + a repo secret
                      and is typed once into the DESK, which decrypts in the
                      browser with WebCrypto. No backend, nothing readable on
                      GitHub.

Why bother: "Define Aesthetics is ready to talk" sat in plain text on GitHub
for two days. A prospect's private conversation status is not ours to publish.

Never raises (a broken desk must not fail a pipeline run).
"""
from __future__ import annotations

import base64
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_RUNS = Path(__file__).resolve().parent.parent / "runs"
PUBLIC = _RUNS / "desk.json"
PRIVATE = _RUNS / "desk.enc.json"
PBKDF2_ITERS = 250_000  # mirrored in command-center components/desk/crypto.ts


def _when(raw: str | None) -> datetime | None:
    try:
        at = datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _iso(at: datetime | None) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ") if at else ""


def _int(v: Any) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _channels(lead: dict) -> list[str]:
    out = []
    if str(lead.get("email_sent", "")).upper() == "TRUE":
        out.append("email")
    if lead.get("whatsapp_stage"):
        out.append("whatsapp")
    if str(lead.get("instagram_sent", "")).upper() == "TRUE":
        out.append("instagram")
    if str(lead.get("linkedin_sent", "")).upper() == "TRUE":
        out.append("linkedin")
    return out


def stage_of(lead: dict) -> str:
    """One status vocabulary for every view (Harvey's rule): each lead is in
    exactly one of these, in funnel order."""
    if str(lead.get("closed_client", "")).lower() == "yes":
        return "client"
    st = lead.get("conversation_stage") or ""
    if str(lead.get("booked_call", "")).lower() == "yes" or st == "booked":
        return "booked"
    if st == "handoff":
        return "handoff"
    if lead.get("status") == "bounced":
        return "bounced"
    if lead.get("opted_out") == "yes" or st == "dead":
        return "dead"
    if lead.get("reply_status") == "interested" or st == "warm":
        return "interested"
    if lead.get("reply_status"):
        return "replied"
    if lead.get("sent_at") or _channels(lead):
        return "contacted"
    return "found"


FUNNEL = ("found", "contacted", "replied", "interested", "handoff", "booked", "client")


def build(leads: list[dict], rejections: list[dict], sending: dict) -> tuple[dict, dict]:
    now = datetime.now(timezone.utc)
    d14 = now - timedelta(days=14)
    rows = []
    for lead in leads:
        sent = _when(lead.get("sent_at"))
        rows.append({
            "company": lead.get("company_name") or "?",
            "niche": lead.get("niche") or "",
            "stage": stage_of(lead),
            "category": lead.get("reply_status") or "",
            "objection": lead.get("objection_type") or "",
            "channels": _channels(lead),
            "followups": _int(lead.get("email_follow_up_count") or lead.get("follow_up_count")),
            "score": lead.get("lead_score") or "",
            "sent_at": _iso(sent),
            "replied_at": _iso(_when(lead.get("replied_at"))),
            "_sent": sent,
        })

    # Cumulative funnel: a lead that booked also replied and was contacted.
    order = {s: i for i, s in enumerate(FUNNEL)}
    funnel = {s: 0 for s in FUNNEL}
    for r in rows:
        depth = order.get(r["stage"], order["contacted"] if r["stage"] in ("bounced", "dead") else 0)
        if r["category"]:  # a "not interested" reply is still a reply
            depth = max(depth, order["replied"])
        for s in FUNNEL[:depth + 1]:
            funnel[s] += 1

    recent = [r for r in rows if r["_sent"] and r["_sent"] >= d14]
    camp: dict[str, dict] = defaultdict(lambda: {"sent": 0, "replied": 0, "interested": 0, "booked": 0})
    for r in rows:
        if r["stage"] == "found":
            continue
        c = camp[r["niche"] or "other"]
        c["sent"] += 1
        c["replied"] += r["stage"] in ("replied", "interested", "handoff", "booked", "client")
        c["interested"] += r["stage"] in ("interested", "handoff", "booked", "client")
        c["booked"] += r["stage"] in ("booked", "client")

    public = {
        "pipeline": "client-acquisition",
        "ts": _iso(now),
        "sending": {"paused": bool(sending.get("paused"))},
        "funnel": funnel,
        "sent_14d": len(recent),
        "bounced_14d": sum(r["stage"] == "bounced" for r in recent),
        "replied_14d": sum(bool(r["category"]) for r in recent),
        "waiting": sum(r["stage"] == "handoff" for r in rows) + bool(sending.get("paused")),
        "gate_blocks": len(rejections),
        "encrypted": PRIVATE.name,
    }
    strip = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    touched = sorted((r for r in rows if r["_sent"]), key=lambda r: r["_sent"], reverse=True)
    private = {
        **public,
        "sending": sending,
        "waiting_on_you": [strip(r) for r in rows if r["stage"] == "handoff"],
        "conversations": sorted((strip(r) for r in rows if r["category"]),
                                key=lambda r: r["replied_at"], reverse=True)[:60],
        "sends": [strip(r) for r in touched[:80]],
        "campaigns": sorted(({"niche": k, **v} for k, v in camp.items()),
                            key=lambda c: c["sent"], reverse=True),
        "gate_rejections": list(reversed(rejections))[:20],
    }
    return public, private


def encrypt(payload: dict, passphrase: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERS).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iters": PBKDF2_ITERS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def write(leads: list[dict] | None = None) -> None:
    from modules import send_gate, sending_control
    try:
        if leads is None:
            from modules import sheets_writer
            leads = sheets_writer.get_all_leads()
        rejections: list[dict] = []
        if send_gate.REJECTIONS.exists():
            for line in send_gate.REJECTIONS.read_text(encoding="utf-8").splitlines()[-20:]:
                try:
                    rejections.append(json.loads(line))
                except ValueError:
                    continue
        public, private = build(leads, rejections, sending_control.status())
        _RUNS.mkdir(parents=True, exist_ok=True)
        PUBLIC.write_text(json.dumps(public, indent=2), encoding="utf-8")
        passphrase = os.getenv("DESK_PASSPHRASE", "").strip()
        if passphrase:
            PRIVATE.write_text(json.dumps(encrypt(private, passphrase)), encoding="utf-8")
        else:
            # No key: publish NOTHING with names rather than plaintext.
            PRIVATE.unlink(missing_ok=True)
            print("    [desk] DESK_PASSPHRASE unset; wrote counts only")
    except Exception as e:  # pragma: no cover - best effort
        print(f"    [desk] failed: {e}")
