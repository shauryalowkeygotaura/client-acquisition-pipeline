"""Run-metrics writer: emits a small JSON the Command Center dashboard reads.

The Command Center is a static GitHub Pages app with no backend, so it cannot
query our process. Instead every pipeline run drops a `runs/latest.json` (and
appends a one-line record to `runs/history.jsonl`) into the repo, and the GitHub
Actions workflow commits it. The dashboard fetches it over raw.githubusercontent.

Schema is shared across all pipelines (philosopher, client-acquisition, ...) so
the dashboard can render any of them with one component:

    {
      "pipeline": "client-acquisition",
      "ts":       "2026-06-04T08:04:13Z",   # UTC ISO8601
      "mode":     "scrape",                  # scrape | replies | ...
      "status":   "ok" | "degraded" | "error",
      "summary":  "human one-liner",
      "metrics":  { ... arbitrary counters ... },
      "budgets":  { "<service>": {"used": int|null, "limit": int, "note": str} }
    }

`status` semantics:
  ok        run did real work (sent/posted something or had nothing new to do)
  degraded  ran clean but produced nothing because of a free-tier wall / quota
  error     unhandled exception bubbled out
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root = parent of the modules/ package this file lives in.
_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

PIPELINE_NAME = "client-acquisition"


def write(
    mode: str,
    status: str,
    summary: str,
    metrics: dict[str, Any] | None = None,
    budgets: dict[str, Any] | None = None,
) -> Path:
    """Write runs/latest.json and append to runs/history.jsonl. Never raises:
    a metrics-write failure must not take down a real pipeline run."""
    payload = {
        "pipeline": PIPELINE_NAME,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "status": status,
        "summary": summary,
        "metrics": metrics or {},
        "budgets": budgets or {},
    }
    try:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        latest = _RUNS_DIR / "latest.json"
        latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with (_RUNS_DIR / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        return latest
    except Exception as e:  # pragma: no cover - best effort
        print(f"    [run_metrics] failed to write metrics: {e}")
        return _RUNS_DIR / "latest.json"


def write_leads(leads: list[dict[str, Any]]) -> Path:
    """Write runs/leads.json — today's qualified leads with their source and the
    channels that auto-fired, for the Command Center lead list. Fetched publicly
    via raw.githubusercontent (same path as latest.json). Never raises."""
    payload = {
        "pipeline": PIPELINE_NAME,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(leads),
        "leads": leads,
    }
    dest = _RUNS_DIR / "leads.json"
    try:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # pragma: no cover - best effort
        print(f"    [run_metrics] failed to write leads: {e}")
    return dest


def write_desk(leads: list[dict[str, Any]] | None = None) -> Path:
    """Write runs/desk.json: the Command Center DESK tab ("is anything waiting
    on me?", Harvey's Today screen, fed by THIS pipeline's data).

    PUBLIC repo, so the same rule as leads.json: business names and counts
    only. No email addresses, no reply text, no person names. Never raises.
    """
    from datetime import timedelta
    from modules import sending_control, send_gate

    dest = _RUNS_DIR / "desk.json"
    try:
        if leads is None:
            from modules import sheets_writer
            leads = sheets_writer.get_all_leads()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=14)

        def _at(lead: dict) -> datetime | None:
            try:
                at = datetime.fromisoformat((lead.get("sent_at") or "").replace("Z", "+00:00"))
            except ValueError:
                return None
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)

        recent = [l for l in leads if (_at(l) or cutoff - timedelta(days=1)) >= cutoff]
        handoffs = sorted(
            ({"company": l.get("company_name") or "?", "niche": l.get("niche") or "",
              "city": l.get("location") or "", "since": l.get("replied_at") or ""}
             for l in leads if l.get("conversation_stage") == "handoff"),
            key=lambda h: h["since"], reverse=True)
        rejections: list[dict] = []
        if send_gate.REJECTIONS.exists():
            for line in send_gate.REJECTIONS.read_text(encoding="utf-8").splitlines()[-10:]:
                try:
                    rejections.append(json.loads(line))
                except ValueError:
                    continue
        payload = {
            "pipeline": PIPELINE_NAME,
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sending": sending_control.status(),
            "handoffs": handoffs,
            "sent_14d": len(recent),
            "bounced_14d": sum(l.get("status") == "bounced" for l in recent),
            "replied_14d": sum(bool(l.get("reply_status")) for l in recent),
            "gate_rejections": list(reversed(rejections)),
        }
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # pragma: no cover - best effort, like write()
        print(f"    [run_metrics] failed to write desk: {e}")
    return dest
