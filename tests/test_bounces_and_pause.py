"""Harvey-borrowed safeguards: bounce detection, auto-pause, and the kill switch
actually stopping every send path (except the handoff notice to Shaurya)."""
from datetime import datetime, timedelta, timezone

import pytest

from modules import email_sender, reply_handler as rh, sending_control


@pytest.fixture(autouse=True)
def _isolated_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(sending_control, "FLAG", tmp_path / "sending_paused.json")
    monkeypatch.delenv("SENDING_PAUSED", raising=False)


def _dsn(addr):
    return {"from_addr": "mailer-daemon@googlemail.com",
            "subject": "Delivery Status Notification (Failure)",
            "body": f"Address not found\n\nYour message wasn't delivered to {addr} because "
                    "the address couldn't be found.", "message_id": "<d@x>", "received_at": None}


def test_bounce_marks_lead_dead_and_is_not_treated_as_reply(monkeypatch):
    updates = []
    monkeypatch.setattr(rh.sheets_writer, "update_field", lambda *a: updates.append(("field",) + a))
    monkeypatch.setattr(rh.sheets_writer, "update_reply", lambda *a, **k: updates.append(("reply",) + a))
    lead = {"slug": "c1", "company_name": "Clinic", "email": "dr@clinic.in", "status": ""}
    real = {"from_addr": "someone@x.com", "subject": "Re: hi", "body": "sure", "message_id": "<r>"}
    n, keep = rh._handle_bounces([_dsn("dr@clinic.in"), real], {"dr@clinic.in": lead})
    assert n == 1 and keep == [real]
    assert ("field", "c1", "status", "bounced") in updates


def test_bounce_rate_over_5_percent_pauses_sending():
    assert sending_control.check_bounce_rate(sent=40, bounced=3) is True
    assert sending_control.is_paused()


def test_small_samples_never_pause():
    assert sending_control.check_bounce_rate(sent=5, bounced=2) is False
    assert not sending_control.is_paused()


def test_guard_counts_only_last_14_days():
    now = datetime.now(timezone.utc)
    recent = [{"sent_at": now.isoformat(), "status": "bounced" if i < 2 else ""} for i in range(30)]
    old = [{"sent_at": (now - timedelta(days=40)).isoformat(), "status": "bounced"} for _ in range(30)]
    rh._bounce_rate_guard(recent + old)          # 2/30 = 6.7% in window -> pause
    assert sending_control.is_paused()


def test_pause_blocks_replies_but_not_the_handoff_notice(monkeypatch):
    sending_control.pause("test")
    sent = []

    class FakeSMTP:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, m): sent.append(m["Subject"])

    monkeypatch.setattr(rh.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(rh, "GMAIL_ADDRESS", "me@x.com")
    monkeypatch.setattr(rh, "GMAIL_APP_PASSWORD", "pw")
    assert rh._send_reply("lead@x.com", "Re: hi", "a perfectly fine reply about the demo. — Shaurya") is False
    assert rh._send_reply("me@x.com", "HANDOFF: Clinic", "lead waiting", kind="notice") is True
    assert sent == ["HANDOFF: Clinic"]


def test_manual_env_pause_blocks_cold_send(monkeypatch):
    monkeypatch.setenv("SENDING_PAUSED", "1")
    ok, _, _ = email_sender.send({"email": "dr@clinic.in", "email_subject": "school project",
                                  "email_body": "hello there, this is a fine body text"})
    assert ok is False
