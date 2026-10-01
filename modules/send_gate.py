"""
modules/send_gate.py — deterministic last check before ANY outbound email.

Borrowed from Harvey (ethanplusai/harvey, gate.py), 2026-10-01. The idea: the
LLM is told the rules in its prompt, but a prompt is advice, not enforcement.
persona.FRAME_BREAKERS has listed "never say clients / our team / I'd love to"
since August, and nothing in code ever checked it. On 2026-09-24 the reply
bot told a clinic owner three different invented reasons it could not name
"the 2 clinics" — every one of which a regex would have caught.

So this runs on the exact text about to leave, with no model involved, and
refuses the send if anything fails. It is the same code path for cold email,
follow-ups and replies. A refusal is logged (runs/gate_rejections.jsonl,
public repo, so company + reason only — never the body or the address).

check() returns a list of reasons. Empty list = clear to send.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from modules import persona

_RUNS = Path(__file__).resolve().parent.parent / "runs"
REJECTIONS = _RUNS / "gate_rejections.jsonl"

# Words caps per kind. Cold prompts aim for ~100-150 words, replies 60-100;
# these leave headroom and only catch a model that rambled off its brief.
MAX_WORDS = {"cold": 230, "followup": 150, "reply": 160}
MAX_LINKS = {"cold": 1, "followup": 1, "reply": 1}

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _frame_breakers() -> list[str]:
    """Quoted phrases from persona.FRAME_BREAKERS: one source of truth, so the
    prompt and the gate can never disagree about what is banned."""
    # Only the dash-bullet lines are the banned list, and anything under 3
    # chars is prose around it (the list says: Always "I"). Banning "i" would
    # block every email, which test_send_gate caught before it shipped.
    out = []
    for line in persona.FRAME_BREAKERS.splitlines():
        if line.lstrip().startswith("-"):
            out += [q.lower() for q in re.findall(r'"([^"]+)"', line) if len(q) >= 3]
    return out


# Made-up reasons for not answering a direct question. All observed or one
# step from observed in the 2026-09-24 thread. There is no NDA, no
# confidentiality agreement, and privacy is not why names weren't shared.
_INVENTED_EXCUSES = [
    r"confidentiality (agreement|clause|rules?)",
    r"\bNDA\b",
    r"(patient|client|student) privacy",
    r"can(no|')?t (share|disclose|name) (their|the) (names?|clinics?)",
    r"bound by",
]

_PLACEHOLDERS = [
    r"\{\{[^}]*\}\}",            # {{company}}
    r"\{[a-z_]+\}",               # {company_name}
    r"\[(?:[A-Z][A-Za-z]*[ _]?){1,4}\]",  # [Company], [First Name]
    r"(?<![\w-])(None|nan|null|undefined)(?![\w-])",
]

_COUNT_CLAIM = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(?:\w+\s+)?"
    r"(clinics?|clients?|businesses|practices|doctors|dentists)\s+"
    r"(?:are\s+|have\s+been\s+)?(?:already\s+)?(using|use|on it)", re.I)


def check(subject: str, body: str, kind: str = "cold") -> list[str]:
    """Every reason this email must not be sent. Pure function; no I/O."""
    reasons: list[str] = []
    text = f"{subject or ''}\n{body or ''}"
    low = text.lower()

    if len((body or "").strip()) < 20:
        reasons.append("empty or near-empty body")

    for pat in _PLACEHOLDERS:
        m = re.search(pat, text)
        if m:
            reasons.append(f"unrendered placeholder: {m.group(0)!r}")
            break

    for phrase in _frame_breakers():
        if re.search(r"(?<![\w'])" + re.escape(phrase) + r"(?![\w'])", low):
            reasons.append(f"frame breaker: {phrase!r}")

    for pat in _INVENTED_EXCUSES:
        m = re.search(pat, text, re.I)
        if m:
            reasons.append(f"invented excuse: {m.group(0)!r}")

    for m in _COUNT_CLAIM.finditer(text):
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        if n != persona.CLIENT_COUNT:
            reasons.append(f"proof claim {n} != PROOF_CLIENT_COUNT {persona.CLIENT_COUNT}")

    words = len((body or "").split())
    if words > MAX_WORDS.get(kind, 230):
        reasons.append(f"{words} words > {MAX_WORDS.get(kind, 230)} cap for {kind}")

    links = len(re.findall(r"https?://", body or ""))
    if links > MAX_LINKS.get(kind, 1):
        reasons.append(f"{links} links > {MAX_LINKS.get(kind, 1)}")

    if re.search(r"<(p|div|br|a|span|html|table)\b", body or "", re.I):
        reasons.append("HTML in a plain-text email")

    return reasons


def record(company: str, kind: str, reasons: list[str]) -> None:
    """Append a rejection for the Command Center desk. Company + reasons only:
    this file is committed to a PUBLIC repo."""
    try:
        _RUNS.mkdir(parents=True, exist_ok=True)
        with REJECTIONS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "company": company or "?", "kind": kind, "reasons": reasons[:5],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
