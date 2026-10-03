"""wa_template.py -- list or submit WhatsApp message templates via the Graph API.

Exists because the WhatsApp Manager UI is not reachable from this account, and
the first cold WhatsApp message must be a Meta-approved template. The Graph API
accepts the same submission the UI would make.

    doppler run -- python scripts/wa_template.py list
    doppler run -- python scripts/wa_template.py submit

The WABA id is discovered from the token's own granular scopes, so nothing
new has to be configured. The token is read from the environment only.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
NAME = "school_project_outreach"
BODY = ("Hi {{1}}, I'm Shaurya, I'm a student at DPS RKP and I'm doing a school "
        "project. Can I show you what I built for {{2}}?")
FOOTER = "Reply STOP to opt out."


def _call(method: str, path: str, token: str, payload: dict | None = None,
          params: dict | None = None) -> dict:
    url = f"{GRAPH}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:500]}")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise SystemExit(f"{method} {path} returned non-JSON: {body[:300]}")


def find_waba(token: str) -> str:
    info = _call("GET", "debug_token", token,
                 params={"input_token": token}).get("data", {})
    for scope in info.get("granular_scopes", []):
        if scope.get("scope") in ("whatsapp_business_management",
                                  "whatsapp_business_messaging"):
            ids = scope.get("target_ids") or []
            if ids:
                return ids[0]
    raise SystemExit("Token carries no WhatsApp Business Account id in its scopes. "
                     f"Scopes seen: {[s.get('scope') for s in info.get('granular_scopes', [])]}")


def main(argv: list[str]) -> int:
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("WHATSAPP_ACCESS_TOKEN not set; run under `doppler run --`.")
    cmd = argv[1] if len(argv) > 1 else "list"
    waba = find_waba(token)
    print(f"WABA {waba}")

    if cmd == "list":
        rows = _call("GET", f"{waba}/message_templates", token,
                     params={"fields": "name,status,category,language", "limit": 50})
        for t in rows.get("data", []):
            print(f"  {t.get('status'):10} {t.get('category'):12} {t.get('language'):6} {t.get('name')}")
        return 0

    if cmd == "submit":
        payload = {
            "name": NAME, "category": "MARKETING", "language": "en",
            "components": [
                {"type": "BODY", "text": BODY,
                 "example": {"body_text": [["there", "your clinic"]]}},
                {"type": "FOOTER", "text": FOOTER},
            ],
        }
        print(json.dumps(_call("POST", f"{waba}/message_templates", token, payload)))
        return 0

    raise SystemExit("usage: wa_template.py [list|submit]")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
