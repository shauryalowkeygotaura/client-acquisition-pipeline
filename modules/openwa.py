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
    text = f"Hi {contact}, are you still looking to fill the receptionist gap at {company}?"
    ok = _post("sendText", {"to": _chat_id(mobile), "content": text})
    if ok:
        log.info("openWA touch 1 sent to %s", mobile)
    return ok


def send_freeform(to_mobile: str, text: str) -> bool:
    """Warm follow-up. (openWA has no Meta 24h-window restriction.)"""
    mobile = _extract_mobile(to_mobile) or to_mobile.lstrip("+")
    return _post("sendText", {"to": _chat_id(mobile), "content": text})


def send_pitch(to_mobile: str, niche: str) -> bool:
    """Touch 2 — the pitch."""
    niche_str = niche or "service"
    text = (
        f"I build voice agents for {niche_str} businesses — they answer calls and "
        f"handle bookings automatically. Want me to send a 2-min clip?"
    )
    return send_freeform(to_mobile, text)
