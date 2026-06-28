"""
modules/learning.py — the self-improving loop.

Every run, read the leads sheet's *outcomes* (who replied, who booked) and turn
them into three learned levers, each gated by modules.significance so the system
acts on real edges, not noise:

  1. variant_by_niche       — which message angle (pain/curiosity/roi/outcome/
                              question) earns the most replies per niche.
  2. channel_order_by_region — which channel (email/linkedin/whatsapp/instagram)
                              earns the most replies, India vs default.
  3. scoring_weights        — nudge the pain/adoption composite toward whichever
                              actually predicts booked calls.

The result is written to runs/learned.json (committed by CI alongside the other
run artifacts). The readers — generator._select_variant, scorer, and pipeline —
load it at the START of the next run, so improvement compounds run-over-run.
Crucially we learn from the PREVIOUS run and apply to the NEXT, never mid-run.

First run (no data / no file): every reader falls back to its hard-coded default,
so the pipeline behaves exactly as before until evidence accumulates.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from modules import sheets_writer, significance

log = logging.getLogger(__name__)

_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
LEARNED_PATH = _RUNS_DIR / "learned.json"

# Minimum sends before an option is even considered (paired with the z-test).
MIN_VARIANT_SENT = int(os.getenv("LEARN_MIN_VARIANT_SENT", "20"))
MIN_CHANNEL_SENT = int(os.getenv("LEARN_MIN_CHANNEL_SENT", "20"))
MIN_WEIGHT_SENT = int(os.getenv("LEARN_MIN_WEIGHT_SENT", "20"))

# Scoring-weight nudge: small, bounded steps so one run can't swing the funnel.
WEIGHT_STEP = 0.05
WEIGHT_MIN, WEIGHT_MAX = 0.30, 0.70

ALL_VARIANTS = ("pain", "curiosity", "roi", "outcome", "question")
DEFAULT_CHANNEL_ORDER = {
    "india": ["whatsapp", "instagram", "email", "linkedin"],
    "default": ["email", "linkedin", "whatsapp", "instagram"],
}
DEFAULT_WEIGHTS = {"pain": 0.6, "adoption": 0.4}

_INDIA_KEYS = (
    "india", "delhi", "mumbai", "bangalore", "bengaluru", "hyderabad",
    "pune", "jaipur", "chennai", "kolkata", "ahmedabad", "kochi",
)


# ── outcome helpers ──────────────────────────────────────────────────────────
def _replied(lead: dict) -> bool:
    rs = (lead.get("reply_status") or "").strip().lower()
    return rs not in ("", "none", "no_reply")


def _booked(lead: dict) -> bool:
    return (lead.get("booked_call") or "").strip().lower() == "yes"


def _region(lead: dict) -> str:
    loc = (lead.get("location") or lead.get("city") or "").lower()
    return "india" if any(k in loc for k in _INDIA_KEYS) else "default"


def _as_int(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


# ── lever 1: best message angle per niche ────────────────────────────────────
def _variant_winners(leads: list[dict]) -> dict[str, str]:
    # niche -> variant -> [sent, replies]
    by: dict[str, dict[str, list[int]]] = {}
    for lead in leads:
        niche = (lead.get("niche") or "unknown").lower()
        variant = (lead.get("message_variant_id") or "").lower()
        if variant not in ALL_VARIANTS:
            continue
        slot = by.setdefault(niche, {}).setdefault(variant, [0, 0])
        slot[0] += 1
        if _replied(lead):
            slot[1] += 1

    winners: dict[str, str] = {}
    for niche, variants in by.items():
        eligible = {v: c for v, c in variants.items() if c[0] >= MIN_VARIANT_SENT}
        if len(eligible) < 2:
            continue  # need at least two contenders with enough data to compare
        best = max(eligible, key=lambda v: eligible[v][1] / eligible[v][0])
        b_sent, b_rep = eligible[best]
        # Pool every OTHER eligible variant as the comparison arm.
        rest_sent = sum(c[0] for v, c in eligible.items() if v != best)
        rest_rep = sum(c[1] for v, c in eligible.items() if v != best)
        if significance.two_proportion_significant(
            b_rep, b_sent, rest_rep, rest_sent, min_n=MIN_VARIANT_SENT
        ):
            winners[niche] = best
    return winners


# ── lever 2: channel order per region ────────────────────────────────────────
def _channel_order(leads: list[dict]) -> dict[str, list[str]]:
    # region -> channel -> [sent, replies]
    by: dict[str, dict[str, list[int]]] = {}
    for lead in leads:
        channel = (lead.get("channel_used") or "").strip().lower()
        if not channel:
            continue
        region = _region(lead)
        slot = by.setdefault(region, {}).setdefault(channel, [0, 0])
        slot[0] += 1
        if _replied(lead):
            slot[1] += 1

    order: dict[str, list[str]] = {}
    for region, base in DEFAULT_CHANNEL_ORDER.items():
        eligible = {
            ch: c for ch, c in by.get(region, {}).items() if c[0] >= MIN_CHANNEL_SENT
        }
        ranked = sorted(
            eligible, key=lambda ch: eligible[ch][1] / eligible[ch][0], reverse=True
        )
        # Ranked-by-evidence channels first, then the rest in default order.
        order[region] = ranked + [ch for ch in base if ch not in ranked]
    return order


# ── lever 3: pain/adoption scoring weights ───────────────────────────────────
def _scoring_weights(leads: list[dict], base: dict[str, float]) -> dict[str, float]:
    hi = [l for l in leads if _as_int(l.get("adoption_score")) >= 5]
    lo = [l for l in leads if _as_int(l.get("adoption_score")) < 5]
    hi_n, lo_n = len(hi), len(lo)
    if hi_n < MIN_WEIGHT_SENT or lo_n < MIN_WEIGHT_SENT:
        return dict(base)

    hi_booked = sum(1 for l in hi if _booked(l))
    lo_booked = sum(1 for l in lo if _booked(l))
    if not significance.two_proportion_significant(
        hi_booked, hi_n, lo_booked, lo_n, min_n=MIN_WEIGHT_SENT
    ):
        return dict(base)

    pain = base.get("pain", DEFAULT_WEIGHTS["pain"])
    # High-adoption leads book more → adoption predicts better → shift toward it.
    if hi_booked / hi_n > lo_booked / lo_n:
        pain -= WEIGHT_STEP
    else:
        pain += WEIGHT_STEP
    pain = max(WEIGHT_MIN, min(WEIGHT_MAX, pain))
    return {"pain": round(pain, 3), "adoption": round(1 - pain, 3)}


# ── public API ───────────────────────────────────────────────────────────────
def load() -> dict:
    """Read the last learned levers. Returns {} if absent/unreadable (defaults)."""
    try:
        return json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run() -> dict:
    """Recompute learned levers from sheet outcomes and persist learned.json.
    Never raises — a learning failure must not take down a real pipeline run."""
    try:
        leads = sheets_writer.get_all_leads()
    except Exception as e:  # pragma: no cover - best effort
        log.error("learning: could not read leads: %s", e)
        return load()
    if not isinstance(leads, list):
        leads = []

    prev = load()
    base_weights = prev.get("scoring_weights") or DEFAULT_WEIGHTS

    learned = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_leads": len(leads),
        "variant_by_niche": _variant_winners(leads),
        "channel_order_by_region": _channel_order(leads),
        "scoring_weights": _scoring_weights(leads, base_weights),
    }

    try:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        LEARNED_PATH.write_text(
            json.dumps(learned, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info(
            "learning: %d niches tuned, weights=%s",
            len(learned["variant_by_niche"]), learned["scoring_weights"],
        )
    except Exception as e:  # pragma: no cover - best effort
        log.error("learning: failed to write learned.json: %s", e)

    return learned
