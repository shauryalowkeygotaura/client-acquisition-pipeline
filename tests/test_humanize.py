"""
Tests for modules/humanize — the last pass before a DM leaves.

These lock down the three things that used to give the outreach away: written
register in a texting window, a demo link mangled by the cleanup itself, and
one paragraph landing as a single broadcast notification.
"""

import re

import pytest

from modules import humanize


# ── register: the copy must stop sounding composed ──────────────────────────

def test_em_dash_never_survives():
    out = humanize.humanize("i build voice agents — they pick up after hours")
    assert "—" not in out and "–" not in out


def test_corporate_vocabulary_is_replaced():
    out = humanize.humanize(
        "I wanted to reach out about a scalable solution to leverage your front desk."
    )
    lowered = out.lower()
    for banned in ("reach out", "solution", "leverage"):
        assert banned not in lowered, f"{banned!r} survived: {out!r}"


def test_email_closers_and_openers_are_stripped():
    out = humanize.humanize(
        "Dear Dr. Shekhawat, I hope this finds you well. "
        "we answer the phone after hours. Let me know if you have any questions! "
        "Best regards,"
    )
    lowered = out.lower()
    assert "dear" not in lowered
    assert "hope this finds you" not in lowered
    assert "any questions" not in lowered
    assert "best regards" not in lowered
    assert "answer the phone after hours" in lowered


def test_only_one_exclamation_mark_survives():
    out = humanize.humanize("hey! saw your clinic! wanted to say hi!")
    assert out.count("!") == 1


def test_lowercase_opener_is_opt_in():
    text = "Saw your clinic page, it is clean."
    assert humanize.humanize(text).startswith("S")
    assert humanize.humanize(text, lowercase_opener=True).startswith("s")


def test_lowercase_opener_leaves_acronyms_and_names_alone():
    # Lowercasing "AI" or a person's name reads as a typo, not as casual.
    assert humanize.humanize("AI answers the phone.", lowercase_opener=True).startswith("AI")


# ── links: the cleanup must never rewrite a URL ─────────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "https://revengine.dev/demo/bright-smile-dental-solutions",
        "https://revengine.dev/demo/robust-physio-elevate",
        "www.revengine.dev/demo/unlock-optimize-clinic",
    ],
)
def test_urls_pass_through_byte_identical(url):
    """
    Hyphens are regex word boundaries, so a naive `\\bsolution\\b` swap would
    turn /demo/bright-smile-dental-solutions into ...-setups and hand the
    prospect a 404. This is the regression that must never come back.
    """
    out = humanize.humanize(f"here you go {url} have a look")
    assert url in out


def test_every_url_is_restored_and_no_placeholder_leaks():
    out = humanize.humanize("see https://revengine.dev/demo/acme and https://x.co/y")
    assert "https://revengine.dev/demo/acme" in out
    assert "https://x.co/y" in out
    assert "\x00" not in out


def test_tells_does_not_fire_on_url_slugs():
    assert humanize.tells("https://revengine.dev/demo/dental-solutions-robust") == []


# ── shape: 2-3 bubbles, not one paragraph ───────────────────────────────────

def test_bubbles_splits_long_copy_on_sentence_boundaries():
    text = (
        "saw your clinic page and the before after posts are good. "
        "i set up the phone line so it picks up when nobody can get to it and books the slot. "
        "want me to send a 2 min clip of it taking a call?"
    )
    parts = humanize.bubbles(text, max_chars=90)
    assert 2 <= len(parts) <= 3
    for part in parts:
        assert part == part.strip()
        # No bubble ends mid-thought.
        assert re.search(r"[.?!]$", part), f"bubble ends mid-sentence: {part!r}"


def test_bubbles_keeps_a_short_message_whole():
    parts = humanize.bubbles("want me to send a 2 min clip?")
    assert parts == ["want me to send a 2 min clip?"]


def test_bubbles_never_drops_the_closing_ask():
    """
    The last bubble is allowed to exceed max_chars. Losing the question at the
    end of a pitch is worse than a final bubble that runs long, so overlong
    copy is fixed in the generator prompt, never by truncating here.
    """
    text = " ".join(f"sentence number {i} here." for i in range(11)) + " want a clip?"
    parts = humanize.bubbles(text, max_chars=40, max_parts=3)
    assert len(parts) == 3
    assert parts[-1].endswith("want a clip?")
    # Every bubble EXCEPT the last respects the cap.
    for part in parts[:-1]:
        assert len(part) <= 40 or " " not in part


def test_bubbles_on_empty_input():
    assert humanize.bubbles("") == []
    assert humanize.bubbles("   ") == []


# ── timing: a reply that lands instantly is a bot whatever it says ──────────

def test_typing_delay_scales_with_length_and_stays_bounded():
    short = humanize.typing_delay_seconds("ok")
    long = humanize.typing_delay_seconds(" ".join(["word"] * 60))
    assert 1.8 <= short <= 22.0
    assert 1.8 <= long <= 22.0
    assert long > short


def test_reply_delay_is_never_instant_and_is_widely_jittered():
    values = [humanize.reply_delay_seconds() for _ in range(200)]
    assert min(values) >= 45
    assert max(values) <= 210
    # A fixed cadence is its own tell, so the spread has to be real: 200 draws
    # from a 166-value window should land on well over half of them.
    assert len(set(values)) > 80
