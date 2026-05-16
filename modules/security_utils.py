"""
security_utils.py — patterns extracted from NeuroLinked V1.3.

Three pure-stdlib utilities:

1. AuditLog        — append-only, HMAC-chained JSON Lines log. Any edit
                     breaks the chain and verify_chain() flags the line.
2. redact_pii      — strip emails / phones / SSNs / CC numbers from
                     strings (and nested dicts/lists) before they hit
                     logs or error reports.
3. verify          — deterministic expectation matcher for plan-step
                     results. Unused at pipeline level today; exported
                     so any future agent-style step gets a free check.

Set AUDIT_HMAC_KEY in the environment (32 random bytes, hex or b64) to
get real tamper evidence. Without it, the chain is still hash-linked but
an attacker who can edit the file can rewrite the whole chain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================================
# 1. Tamper-evident audit log
# ============================================================================

@dataclass
class AuditRecord:
    ts: float
    actor: str
    action: str
    target: str = ""
    ok: bool = True
    detail: Dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""


class AuditLog:
    """
    Append-only audit log. Each record's hash chains from the previous,
    so editing record N breaks every record from N onward.

    Thread-safe (single-process). For cross-process use, point each
    process at a different path or wrap with a file lock.
    """

    def __init__(self, path: str, hmac_key: Optional[bytes] = None):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._key = hmac_key or self._load_key_from_env()
        self._lock = threading.Lock()
        self._last_hash = self._scan_last_hash()

    @staticmethod
    def _load_key_from_env() -> bytes:
        raw = os.getenv("AUDIT_HMAC_KEY", "")
        if raw:
            try:
                return bytes.fromhex(raw)
            except ValueError:
                return raw.encode("utf-8")
        return b"client-acq-audit-default"

    def _scan_last_hash(self) -> str:
        if not os.path.exists(self.path):
            return ""
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 8192))
                lines = f.read().splitlines()
            for line in reversed(lines):
                if line.strip():
                    return json.loads(line).get("hash", "")
        except Exception:
            return ""
        return ""

    def _compute_hash(self, rec: AuditRecord) -> str:
        body = json.dumps({
            "ts": rec.ts, "actor": rec.actor, "action": rec.action,
            "target": rec.target, "ok": rec.ok, "detail": rec.detail,
            "prev_hash": rec.prev_hash,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, body, hashlib.sha256).hexdigest()

    def append(self, actor: str, action: str, target: str = "",
               ok: bool = True, detail: Optional[dict] = None) -> AuditRecord:
        detail = redact_pii(detail or {})
        with self._lock:
            rec = AuditRecord(
                ts=time.time(), actor=actor, action=action,
                target=target, ok=ok, detail=detail,
                prev_hash=self._last_hash,
            )
            rec.hash = self._compute_hash(rec)
            self._last_hash = rec.hash
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), separators=(",", ":")) + "\n")
            return rec

    def verify_chain(self) -> dict:
        if not os.path.exists(self.path):
            return {"ok": True, "records": 0, "first_break_line": None}
        prev_hash, i = "", 0
        with open(self.path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("prev_hash") != prev_hash:
                    return {"ok": False, "records": i,
                            "first_break_line": i, "reason": "prev_hash mismatch"}
                recomputed = self._compute_hash(AuditRecord(
                    ts=rec["ts"], actor=rec["actor"], action=rec["action"],
                    target=rec["target"], ok=rec["ok"], detail=rec["detail"],
                    prev_hash=rec["prev_hash"],
                ))
                if recomputed != rec.get("hash"):
                    return {"ok": False, "records": i,
                            "first_break_line": i, "reason": "body hash mismatch"}
                prev_hash = rec["hash"]
        return {"ok": True, "records": i, "first_break_line": None}

    def tail(self, n: int = 100) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        return [json.loads(l) for l in lines[-n:]]


_DEFAULT_AUDIT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "audit", "sends.jsonl",
)
_audit_singleton: Optional[AuditLog] = None


def get_audit_log() -> AuditLog:
    """Process-wide AuditLog. Path override via AUDIT_LOG_PATH env."""
    global _audit_singleton
    if _audit_singleton is None:
        path = os.getenv("AUDIT_LOG_PATH", _DEFAULT_AUDIT_PATH)
        _audit_singleton = AuditLog(path)
    return _audit_singleton


# ============================================================================
# 2. PII redaction
# ============================================================================

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN_RE   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE    = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b")


def redact_text(s: Any) -> Any:
    """Redact PII from a single string. Non-strings pass through unchanged.

    Order: email, SSN, CC, phone. SSN/CC must run before phone because
    the phone regex is permissive enough to swallow them otherwise.
    """
    if not isinstance(s, str):
        return s
    s = _EMAIL_RE.sub("[REDACTED_EMAIL]", s)
    s = _SSN_RE.sub("[REDACTED_SSN]", s)
    s = _CC_RE.sub("[REDACTED_CARD]", s)
    s = _PHONE_RE.sub("[REDACTED_PHONE]", s)
    return s


def redact_pii(obj: Any) -> Any:
    """Walk dict/list recursively, redact strings. Returns a new object."""
    if isinstance(obj, dict):
        return {k: redact_pii(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_pii(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_pii(x) for x in obj)
    return redact_text(obj)


# ============================================================================
# 3. Deterministic plan-step verifier (exported for future use)
# ============================================================================

def verify(result: dict, expect: Optional[str]) -> bool:
    """
    Expectation matcher for agent-style step results.

      None / ""       -> bool(result["ok"])
      "ok"            -> bool(result["ok"])
      "field=value"   -> exact string match
      "field>0" etc   -> numeric comparison (>, <, >=, <=, !=)
    """
    if not expect:
        return bool(result.get("ok"))
    expect = expect.strip()
    if expect == "ok":
        return bool(result.get("ok"))
    if "=" in expect and not any(c in expect for c in "<>!"):
        k, v = expect.split("=", 1)
        return str(result.get(k.strip())) == v.strip()
    for op in (">=", "<=", ">", "<", "!="):
        if op in expect:
            k, v = expect.split(op, 1)
            try:
                kv = float(result.get(k.strip(), 0))
                vv = float(v.strip())
            except Exception:
                return False
            return {
                ">":  kv >  vv, "<":  kv <  vv,
                ">=": kv >= vv, "<=": kv <= vv,
                "!=": kv != vv,
            }[op]
    return False
