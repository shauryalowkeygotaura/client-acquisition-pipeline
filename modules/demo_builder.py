"""
modules/demo_builder.py - "hear your own receptionist" per-lead voice config.

Feature (c) of the integration hub. For one QUALIFIED lead it builds a small
PARTIAL OVERRIDE that the jio-voice-demo front end (web_demo/api/turn.py
_load_cfg(slug)) deep-merges over its base agent_config.json, so the public
demo opens speaking as THIS clinic's front desk and reuses the lead's already
generated vapi_prompt verbatim.

Contract (Deliverables/integration-contract-2026-06-28.md, perLeadConfigSchema):
  Required keys : slug, company_name, first_message, system_prompt
  Optional      : tts_voice (default en-IN-NeerjaNeural)
  NEVER emitted : model / temperature / max_tokens / tools / tool_speech
                  (those are ALWAYS inherited from base - protects the tool
                  loop + Groq quota, and can't be overridden per lead).

Slug resolution is WHITELIST-VALIDATION, never strip-bad-chars:
  ^[a-z0-9-]{1,64}$ is the only accepted shape. A lead slug that fails the
  pattern is treated as "no slug" and re-derived from company_name via the same
  slugify() video_pitch uses, so the config stem, the mp4 filename, and the
  demo_url slug always agree.

This module is keyless and offline: it only reads the lead dict and writes a
small JSON file. No network, no secrets.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Reuse the SAME slugify video_pitch uses for the mp4 filename so the config
# stem and the video filename are guaranteed identical for a given lead.
from modules.video_pitch import slugify

# ── Contract constants ───────────────────────────────────────────────────────
# Whitelist is the ONLY accepted slug shape (slugFormat in the contract).
SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
# Host is fixed by .vercel/project.json (projectName jio-voice-demo). Overridable
# via env only so a fork/preview deploy can point elsewhere without code edits.
DEMO_HOST = os.getenv("DEMO_HOST", "jio-voice-demo.vercel.app").strip()
DEFAULT_TTS_VOICE = "en-IN-NeerjaNeural"


class DemoConfigError(Exception):
    pass


def config_dir() -> Path:
    """Where per-lead overrides are written.

    The configs are repo-tracked in the jio-voice-demo repo at
    web_demo/api/configs/ and ship at deploy time (not fetched at runtime, so
    the serverless path stays keyless). This producer repo points at that dir
    via DEMO_CONFIG_DIR; absent that, it stages them under runs/demo_configs so
    a local run never writes outside this repo.
    """
    env = os.getenv("DEMO_CONFIG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "runs" / "demo_configs"


def resolve_slug(lead: dict) -> str:
    """Whitelist-validated slug. Mirrors video_pitch.make()'s precedence
    (explicit lead['slug'] first, else slugify(company_name)) but ENFORCES the
    whitelist: an explicit slug that fails the pattern is discarded, never
    sanitized-then-used, and we fall back to the derived form."""
    explicit = (lead.get("slug") or "").strip()
    if explicit and SLUG_RE.match(explicit):
        return explicit
    name = lead.get("company_name") or lead.get("label") or "lead"
    derived = (slugify(name) or "lead")[:64].strip("-") or "lead"
    if not SLUG_RE.match(derived):
        return "lead"
    return derived


def demo_url(slug: str) -> str:
    """Deterministic, non-scammy, path-based demo link for a slug.

    Pure function of the slug (assetLifecycle (c)). Whitelist-validates the
    input so a bad slug can never build a malformed/host-confusing URL."""
    if not SLUG_RE.match(slug or ""):
        raise DemoConfigError(f"refusing to build demo_url for non-whitelist slug: {slug!r}")
    return f"https://{DEMO_HOST}/demo/{slug}"


def _greeting(company: str) -> str:
    """First message so the agent opens as THIS clinic's front desk. Hinglish to
    match the India SMB demo voice (en-IN-Neerja). No em dashes, no buzzwords."""
    company = (company or "the clinic").strip()
    return (f"Namaste! Aap {company} se baat kar rahe hain. "
            f"Main aapki kaise madad kar sakti hoon?")


def build_config(lead: dict) -> dict:
    """Build the PARTIAL override dict matching perLeadConfigSchema.

    Raises DemoConfigError when the lead has no vapi_prompt: the whole point of
    this feature is to reuse the lead's already-generated prompt, and a config
    that blanked out the base system_prompt would break the agent. Callers
    (warm_mockup) treat that as a skip, never a crash.
    """
    slug = resolve_slug(lead)
    company = (lead.get("company_name") or lead.get("label") or "").strip()
    if not company:
        raise DemoConfigError("lead has no company_name - cannot build demo config")
    prompt = (lead.get("vapi_prompt") or "").strip()
    if not prompt:
        raise DemoConfigError(
            f"lead {slug} has no vapi_prompt - nothing to reuse, skipping config")

    cfg = {
        "slug": slug,
        "company_name": company,
        "first_message": _greeting(company),
        # Verbatim reuse of the lead's existing per-lead prompt (Sheet column).
        "system_prompt": prompt,
        "tts_voice": (lead.get("tts_voice") or DEFAULT_TTS_VOICE).strip(),
    }
    return cfg


def write_config(lead: dict, out_dir: str | Path | None = None) -> Path:
    """Build + write web_demo/api/configs/<slug>.json. Returns the path.

    The filename stem equals cfg['slug'] (== the mp4 filename == the demo_url
    slug). ensure_ascii=False keeps the Hinglish greeting readable in the file.
    """
    cfg = build_config(lead)
    d = Path(out_dir) if out_dir else config_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{cfg['slug']}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    # Offline smoke test - no creds, no network. Prints the override + url.
    demo_lead = {
        "company_name": "Smile Dental Studio",
        "slug": "smile-dental-jaipur",
        "vapi_prompt": "You are the friendly front desk for Smile Dental Studio...",
    }
    print(json.dumps(build_config(demo_lead), ensure_ascii=False, indent=2))
    print(demo_url(resolve_slug(demo_lead)))
