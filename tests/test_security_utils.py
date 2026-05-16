"""Tests for modules.security_utils — patterns from NeuroLinked V1.3."""

import json
import os
import tempfile

import pytest

from modules.security_utils import (
    AuditLog,
    get_audit_log,
    redact_pii,
    redact_text,
    verify,
)


# ── redact ──────────────────────────────────────────────────────────────────

def test_redact_text_email():
    assert redact_text("contact me at jane@acme.io") == "contact me at [REDACTED_EMAIL]"


def test_redact_text_phone():
    assert "[REDACTED_PHONE]" in redact_text("call (415) 555-1212 tomorrow")


def test_redact_text_ssn_before_phone():
    """SSN must redact before phone — phone regex would otherwise swallow it."""
    out = redact_text("SSN 123-45-6789 ok")
    assert "[REDACTED_SSN]" in out
    assert "[REDACTED_PHONE]" not in out


def test_redact_text_passthrough_non_string():
    assert redact_text(42) == 42
    assert redact_text(None) is None


def test_redact_pii_recursive():
    obj = {"to": "jane@acme.io", "meta": {"phones": ["415-555-1212"]}, "score": 7}
    out = redact_pii(obj)
    assert out["to"] == "[REDACTED_EMAIL]"
    assert "[REDACTED_PHONE]" in out["meta"]["phones"][0]
    assert out["score"] == 7  # numbers untouched


# ── audit log: append + verify roundtrip ────────────────────────────────────

@pytest.fixture
def tmp_audit(tmp_path):
    return AuditLog(str(tmp_path / "audit.jsonl"), hmac_key=b"test-key")


def test_append_chains_hashes(tmp_audit):
    r1 = tmp_audit.append("test", "send", "acme")
    r2 = tmp_audit.append("test", "send", "globex")
    assert r1.prev_hash == ""
    assert r2.prev_hash == r1.hash
    assert r1.hash != r2.hash


def test_verify_chain_clean(tmp_audit):
    tmp_audit.append("test", "send", "acme")
    tmp_audit.append("test", "send", "globex")
    result = tmp_audit.verify_chain()
    assert result["ok"] is True
    assert result["records"] == 2


def test_verify_chain_detects_tamper(tmp_audit):
    tmp_audit.append("test", "send", "acme")
    tmp_audit.append("test", "send", "globex")
    # Rewrite the first record's target without recomputing hash
    with open(tmp_audit.path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    rec = json.loads(lines[0])
    rec["target"] = "hijacked"
    lines[0] = json.dumps(rec) + "\n"
    with open(tmp_audit.path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    result = tmp_audit.verify_chain()
    assert result["ok"] is False
    assert result["first_break_line"] == 1


def test_append_redacts_detail(tmp_audit):
    rec = tmp_audit.append("test", "send", "acme",
                           detail={"to": "jane@acme.io", "score": 7})
    assert rec.detail["to"] == "[REDACTED_EMAIL]"
    assert rec.detail["score"] == 7


def test_get_audit_log_singleton(monkeypatch, tmp_path):
    import modules.security_utils as su
    monkeypatch.setattr(su, "_audit_singleton", None)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "s.jsonl"))
    a = get_audit_log()
    b = get_audit_log()
    assert a is b


# ── verify (expectation matcher) ────────────────────────────────────────────

@pytest.mark.parametrize("result,expect,want", [
    ({"ok": True}, None, True),
    ({"ok": False}, None, False),
    ({"ok": True}, "ok", True),
    ({"status": "200"}, "status=200", True),
    ({"status": "500"}, "status=200", False),
    ({"row_count": 5}, "row_count>0", True),
    ({"row_count": 0}, "row_count>0", False),
    ({"row_count": 3}, "row_count<=3", True),
    ({"row_count": 4}, "row_count<=3", False),
])
def test_verify_matrix(result, expect, want):
    assert verify(result, expect) is want
