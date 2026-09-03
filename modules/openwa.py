"""
modules/openwa.py — WhatsApp outreach via open-wa/wa-automate (unofficial).

Alternative to modules/whatsapp.py (Meta Cloud API). openWA drives WhatsApp Web
through a *personal* number — no Meta business verification, no template approval,
free — by talking to a local Node "EASY API" server.

  ⚠️  TRADE-OFF: this is the unofficial route and is against WhatsApp's ToS.
      It carries a real ban risk for the number used. Use a burner/secondary
      number, keep volume low, respect opt-outs, and only message businesses
      that plausibly want to hear from you. The Meta path (whatsapp.py) is the
      compliant default; openWA is opt-in via WHATSAPP_PROVIDER=openwa.

Same public interface as whatsapp.py so the cascade can route to either:
  send(data) -> bool                  # cold touch 1 (question only)
  send_freeform(to_mobile, text)      # warm follow-up (no 24h-window limit here)
  send_pitch(to_mobile, niche)        # touch 2

Run the Node server once (separate terminal), scan the QR with the burner phone:
  npx @open-wa/wa-automate --api --api-host 0.0.0.0 --port 8002 \
      --key "$OPENWA_API_KEY" --session-data-only

Doppler secrets:
  OPENWA_API_URL   default http://localhost:8002
  OPENWA_API_KEY   the --key you started the server with
"""
import logging
import os
import re

from modules import persona

import requests

log = logging.getLogger(__name__)

API_URL = os.getenv("OPENWA_API_URL", "http://localhost:8002").rstrip("/")
API_KEY = os.getenv("OPENWA_API_KEY", "")

# Indian mobile: +91 prefix optional, 10 digits starting 6–9 (same rule as whatsapp.py)
_INDIA_MOBILE_RE = re.compile(r"(?:\+91[\s\-]?)?([6-9]\d{9})")


def _extract_mobile(phone: str) -> str | None:
    """Return bare E.164 digits (91XXXXXXXXXX) or None."""
    if not phone:
        return None
    m = _INDIA_MOBILE_RE.search(re.sub(r"\s", "", phone))
    return f"91{m.group(1)}" if m else None


def _chat_id(mobile: str) -> str:
    """open-wa chat id format: <number>@c.us."""
    return f"{mobile.lstrip('+')}@c.us"


def _post(endpoint: str, args: dict) -> bool:
    """POST to the EASY API. Body shape is {"args": {...}}; auth via api_key header."""
    if not API_KEY:
        log.debug("openWA not configured (OPENWA_API_KEY missing)")
        return False
    try:
        resp = requests.post(
            f"{API_URL}/{endpoint}",
            json={"args": args},
            headers={"api_key": API_KEY, "Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code == 200:
            # EASY API returns {"success": true, "response": ...} on send.
            ok = bool(resp.json().get("success", True))
            if not ok:
                log.error("openWA %s returned success=false: %s", endpoint, resp.text[:200])
            return ok
        log.error("openWA %s HTTP %s: %s", endpoint, resp.status_code, resp.text[:200])
        return False
    except Exception as e:  # noqa: BLE001
        log.error("openWA %s failed: %s", endpoint, e)
        return False


def send(data: dict) -> bool:
    """Cold touch 1 — question only, no pitch/link (Hormozi 2-message rule)."""
    mobile = _extract_mobile(data.get("phone", ""))
    if not mobile:
        log.debug("No valid Indian mobile for %s — skipping openWA", data.get("company_name"))
        return False
    contact = data.get("poster_name") or "there"
    company = data.get("company_name", "your business")
    # Touch 1 carries the OPENER only — who he is and why he's writing — and asks
    # nothing but permission. The demo, the proof and the meeting all come later.
    # Wording mirrors modules/persona.py; keep them in step.
    text = (
        f"Hi {contact}, I'm {persona.SENDER_NAME}, I'm a student at {persona.SCHOOL_SHORT} "
        f"and I'm doing a school project. Can I show you what I built for {company}?"
    )
    ok = _post("sendText", {"to": _chat_id(mobile), "content": text})
    if ok:
        log.info("openWA touch 1 sent to %s", mobile)
    return ok


def send_freeform(to_mobile: str, text: str) -> bool:
    """Warm follow-up. (openWA has no Meta 24h-window restriction.)"""
    mobile = _extract_mobile(to_mobile) or to_mobile.lstrip("+")
    return _post("sendText", {"to": _chat_id(mobile), "content": text})


def send_video(to_mobile: str, path, caption: str = "") -> bool:
    """Send a native mp4 (cold distribution video) via the EASY API sendFile
    endpoint. Reads the file as a base64 data URI. Returns False on any failure."""
    import base64
    from pathlib import Path as _Path

    mp4 = _Path(path)
    if not mp4.exists():
        log.warning("openWA video missing: %s", path)
        return False
    mobile = _extract_mobile(to_mobile) or to_mobile.lstrip("+")
    try:
        b64 = base64.b64encode(mp4.read_bytes()).decode("ascii")
    except OSError as e:
        log.error("openWA could not read video %s: %s", path, e)
        return False
    return _post("sendFile", {
        "to": _chat_id(mobile),
        "file": f"data:video/mp4;base64,{b64}",
        "filename": mp4.name,
        "caption": caption,
    })


def send_pitch(to_mobile: str, niche: str) -> bool:
    """Touch 2 — what it is, the proof, and the free demo ask.

    Fallback only. The Meta path passes the generator's per-lead `whatsapp_msg`
    into whatsapp.send_pitch; this generic version exists so an openWA-only run
    still says something in the right voice rather than nothing.
    """
    niche_str = niche or "service"
    proof = persona.proof_line()
    proof_sentence = f" {proof[0].upper()}{proof[1:]}." if proof else ""
    text = (
        f"I built a thing that picks up the phone for {niche_str} places when nobody "
        f"can get to it and books the appointment.{proof_sentence} "
        f"I can set one up on your details for free too. "
        f"Can I talk to you about it sometime? Just want some feedback on it."
    )
    return send_freeform(to_mobile, text)
