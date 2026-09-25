"""Regression tests for the 2026-09-24 reply flood (one lead, 16 replies)."""
from datetime import datetime, timedelta, timezone

from modules import jev, reply_handler as rh

T0 = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


def _msg(addr, minutes, body="Saturday 10 AM works."):
    return {"from_addr": addr, "subject": "Re: school project", "body": body,
            "message_id": f"<{minutes}@x>", "received_at": T0 + timedelta(minutes=minutes)}


def _lead(**kw):
    base = {"slug": "define-aesthetics", "email": "dr@x.com", "company_name": "Define",
            "conversation_stage": "warm", "opted_out": "no", "replied_at": ""}
    return {**base, **kw}


def _wire(monkeypatch, inbox, lead, classification):
    sent, updates, notices = [], [], []
    monkeypatch.setattr(rh, "_fetch_inbox_replies", lambda since: inbox)
    monkeypatch.setattr(rh.sheets_writer, "get_all_leads", lambda: [lead])
    monkeypatch.setattr(rh.sheets_writer, "update_reply",
                        lambda slug, *a, **k: updates.append((a, k)))
    monkeypatch.setattr(rh, "_classify_reply", lambda text: classification)
    monkeypatch.setattr(rh, "_generate_response", lambda *a, **k: "hi")
    monkeypatch.setattr(rh, "_send_reply", lambda to, subj, body, **k: sent.append(to) or True)
    monkeypatch.setattr(rh, "_notify_handoff", lambda *a: notices.append(a))
    return sent, updates, notices


def test_four_inbound_messages_get_one_reply(monkeypatch):
    inbox = [_msg("dr@x.com", m) for m in (0, 5, 10, 15)]
    sent, _, _ = _wire(monkeypatch, inbox, _lead(),
                       {"category": "neutral", "ready_to_meet": False})
    rh.run()
    assert sent == ["dr@x.com"]


def test_message_older_than_last_reply_is_skipped(monkeypatch):
    lead = _lead(replied_at=(T0 + timedelta(hours=1)).isoformat())
    sent, _, _ = _wire(monkeypatch, [_msg("dr@x.com", 0)], lead,
                       {"category": "interested", "ready_to_meet": False})
    rh.run()
    assert sent == []


def test_ready_to_meet_hands_off_instead_of_replying(monkeypatch):
    sent, updates, notices = _wire(monkeypatch, [_msg("dr@x.com", 0)], _lead(),
                                   {"category": "interested", "ready_to_meet": True})
    stats = rh.run()
    assert sent == [] and len(notices) == 1 and stats["handoffs"] == 1
    assert updates[0][0][1] == "handoff"


def test_handoff_stage_is_never_answered(monkeypatch):
    sent, _, _ = _wire(monkeypatch, [_msg("dr@x.com", 0)], _lead(conversation_stage="handoff"),
                       {"category": "interested", "ready_to_meet": False})
    rh.run()
    assert sent == []


def test_jev_failure_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(jev, "available", lambda: True)

    def boom(*a, **k):
        raise jev.JevUnavailable("down")
    monkeypatch.setattr(jev, "evaluate", boom)
    monkeypatch.setattr(rh, "GROQ_API_KEY", None)
    assert rh._classify_reply("hello")["category"] == "neutral"


def test_jev_answers_are_mapped(monkeypatch):
    monkeypatch.setattr(jev, "available", lambda: True)
    monkeypatch.setattr(jev, "evaluate", lambda state, q: {
        "category": {"choice": "interested"},
        "objection_type": {"choice": "none"},
        "ready_to_meet": {"noul": 0.91},
    })
    out = rh._classify_reply("Saturday works, send the link")
    assert out["category"] == "interested" and out["objection_type"] == ""
    assert out["ready_to_meet"] is True
