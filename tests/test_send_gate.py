"""send_gate against REAL text: the emails that went wrong, and the templates
that must keep flowing. A gate that blocks the good mail is as broken as one
that lets the bad mail through."""
import pytest

from modules import persona, reply_handler, send_gate

# Sent to Dr. Sheetal Badami, 2026-09-21..24 (verbatim from Gmail Sent).
BAD_REPLIES = [
    "Regarding the clinics, since this is a school project, I'm currently bound by strict "
    "student confidentiality agreements, so I can't disclose their names or share a public "
    "demo link yet.",
    "Regarding the clinics, I can't share their names due to patient privacy, but I can walk "
    "you through exactly how the agent handles after-hours booking.",
    "That's actually great news. Your experience running clinics for years is exactly the "
    "perspective I need. I'd love to show you how the agent handles patient workflows.",
]

# The cold email that opened that thread, which was fine.
GOOD_COLD = (
    "I'm Shaurya, I'm in school at DPS RKP, and I built something for a school project.\n\n"
    "I built a voice agent that picks up the clinic phone when nobody can get to it, answers "
    "the usual questions, books the appointment, and texts you the details.\n\n"
    f"{persona.CLIENT_COUNT} clinics are already using it.\n\n"
    "Could I talk to you about it sometime? I mainly just want feedback from someone who'd know.\n\n"
    "— Shaurya"
)


@pytest.mark.parametrize("body", BAD_REPLIES)
def test_the_sheetal_replies_are_blocked(body):
    assert send_gate.check("Re: school project", body, "reply")


def test_the_real_cold_email_passes():
    assert send_gate.check("school project", GOOD_COLD, "cold") == []


@pytest.mark.parametrize("body", list(reply_handler._OBJECTION_REBUTTALS.values())
                         + [reply_handler._DEFAULT_REPLY])
def test_hardcoded_fallback_replies_pass(body):
    assert send_gate.check("Re: school project", body, "reply") == []


def test_inflated_proof_claim_blocked():
    body = GOOD_COLD.replace(f"{persona.CLIENT_COUNT} clinics", f"{persona.CLIENT_COUNT + 3} clinics")
    assert any("proof claim" in r for r in send_gate.check("x", body, "cold"))


def test_unrendered_placeholder_blocked():
    assert send_gate.check("x", "Hi {{contact_name}}, quick question about [Company] and your desk.", "cold")


def test_two_links_blocked_in_reply():
    body = "Here is the demo https://a.example and the link https://b.example, see you then. — Shaurya"
    assert any("links" in r for r in send_gate.check("x", body, "reply"))
