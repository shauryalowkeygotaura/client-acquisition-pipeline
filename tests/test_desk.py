"""desk.json is PUBLIC; desk.enc.json must decrypt with the passphrase only."""
import base64
import json

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from modules import desk

LEADS = [
    {"company_name": "Secret Smile Clinic", "niche": "dental", "sent_at": "2026-09-30T10:00:00+00:00",
     "email_sent": "TRUE", "reply_status": "interested", "conversation_stage": "handoff"},
    {"company_name": "Quiet Dental", "niche": "dental", "sent_at": "2026-09-30T10:00:00+00:00",
     "email_sent": "TRUE", "reply_status": "not_relevant", "conversation_stage": "dead"},
    {"company_name": "Never Mailed", "niche": "physio"},
]


def _decrypt(blob, passphrase):
    b = base64.b64decode
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b(blob["salt"]),
                     iterations=blob["iters"]).derive(passphrase.encode())
    return json.loads(AESGCM(key).decrypt(b(blob["iv"]), b(blob["ct"]), None))


def test_public_file_contains_no_names():
    public, _ = desk.build(LEADS, [{"company": "Blocked Co", "reasons": ["x"]}], {"paused": False})
    text = json.dumps(public)
    for name in ("Secret Smile", "Quiet Dental", "Never Mailed", "Blocked Co"):
        assert name not in text


def test_funnel_is_cumulative_and_counts_negative_replies():
    public, _ = desk.build(LEADS, [], {"paused": False})
    f = public["funnel"]
    assert f["found"] == 3 and f["contacted"] == 2
    assert f["replied"] == 2          # the "not interested" reply still counts
    assert f["handoff"] == 1 and public["waiting"] == 1


def test_private_round_trip_and_wrong_key_fails():
    _, private = desk.build(LEADS, [], {"paused": False})
    blob = desk.encrypt(private, "correct horse")
    assert "Secret Smile" not in json.dumps(blob)
    assert _decrypt(blob, "correct horse")["waiting_on_you"][0]["company"] == "Secret Smile Clinic"
    with pytest.raises(InvalidTag):
        _decrypt(blob, "wrong")


def test_no_passphrase_means_no_private_file(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "PUBLIC", tmp_path / "desk.json")
    monkeypatch.setattr(desk, "PRIVATE", tmp_path / "desk.enc.json")
    monkeypatch.setattr(desk, "_RUNS", tmp_path)
    monkeypatch.delenv("DESK_PASSPHRASE", raising=False)
    (tmp_path / "desk.enc.json").write_text("stale")
    desk.write(LEADS)
    assert (tmp_path / "desk.json").exists() and not (tmp_path / "desk.enc.json").exists()
