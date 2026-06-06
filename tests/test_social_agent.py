"""
Offline tests for the free social agent. No network, no Groq key, no Sheets.
A FakeClient stands in for the Groq/OpenAI client so the LLM paths are covered
deterministically; the no-key fallback paths are covered too.
"""
import json

import pytest

from modules import command_center, content_engine, social_brain
from modules.social_state import SocialState
from modules.connectors import load_connectors
from modules.connectors.base import Connector, InboundItem, PostResult


# ── fakes ──────────────────────────────────────────────────────────────────────
class _Msg:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _Resp:
    def __init__(self, content): self.choices = [_Msg(content)]


class FakeClient:
    """Mimics openai.OpenAI: .chat.completions.create(...) -> resp."""
    def __init__(self, content):
        self._content = content
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kwargs):
        return _Resp(self._content)


class FakeConnector(Connector):
    name = "fake"

    def __init__(self, inbound=None):
        self._inbound = inbound or []
        self.posted = []
        self.replies = []

    def available(self): return True

    def post(self, text, media=None):
        self.posted.append(text)
        return PostResult(ok=True, platform=self.name, item_id="p1")

    def fetch_inbound(self, state):
        out = [i for i in self._inbound if not state.is_seen(self.name, i.item_id)]
        return out

    def reply(self, item, text):
        self.replies.append((item.item_id, text))
        return True


# ── social_state ────────────────────────────────────────────────────────────────
def test_state_dedupe_and_cursor(tmp_path):
    st = SocialState(tmp_path / "s.json")
    assert not st.is_seen("telegram", "42")
    st.mark_seen("telegram", "42")
    assert st.is_seen("telegram", "42")
    st.set_cursor("telegram", 99)
    st.save()
    # reload from disk → state persists
    st2 = SocialState(tmp_path / "s.json")
    assert st2.is_seen("telegram", "42")
    assert st2.get_cursor("telegram") == 99


def test_state_seen_list_is_bounded(tmp_path):
    st = SocialState(tmp_path / "s.json")
    for i in range(600):
        st.mark_seen("x", str(i))
    seen = st._platform("x")["seen"]
    assert len(seen) <= 500
    assert st.is_seen("x", "599")          # newest kept
    assert not st.is_seen("x", "0")        # oldest trimmed


# ── registry ────────────────────────────────────────────────────────────────────
def test_registry_defaults_to_console(monkeypatch):
    monkeypatch.delenv("SOCIAL_PLATFORMS", raising=False)
    conns = load_connectors(verbose=False)
    assert any(c.name == "console" for c in conns)


def test_registry_skips_unconfigured(monkeypatch):
    monkeypatch.setenv("SOCIAL_PLATFORMS", "telegram")  # no token set
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    conns = load_connectors(verbose=False)
    # telegram drops out, falls back to console
    assert [c.name for c in conns] == ["console"]


# ── console connector ───────────────────────────────────────────────────────────
def test_console_inbound_reads_and_dedupes(tmp_path, monkeypatch):
    from modules.connectors import console
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        json.dumps({"id": "1", "text": "do you build this for clinics?", "author": "doc"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(console, "_INBOX", inbox)
    c = console.ConsoleConnector()
    st = SocialState(tmp_path / "s.json")

    items = c.fetch_inbound(st)
    assert len(items) == 1 and items[0].author_handle == "doc"

    st.mark_seen("console", "1")
    assert c.fetch_inbound(st) == []   # already seen → not returned again


# ── content_engine ──────────────────────────────────────────────────────────────
def test_generate_post_uses_client():
    out = content_engine.generate_post("build-log", "a thing that broke",
                                       client=FakeClient("Real clinics break it fast. Here is the fix."))
    assert "clinic" in out.lower()


def test_generate_post_fallback_without_key(monkeypatch):
    monkeypatch.setattr(content_engine, "GROQ_API_KEY", "")
    out = content_engine.generate_post("s", "missed calls cost money")
    assert "missed calls cost money" in out


# ── social_brain ────────────────────────────────────────────────────────────────
def test_classify_lead_via_client():
    client = FakeClient(json.dumps({"category": "lead", "niche": "dental",
                                    "intent": "high", "summary": "wants pricing"}))
    cls = social_brain.classify("how much per month for my dental clinic?",
                                author="doc", client=client)
    assert cls["category"] == "lead" and cls["niche"] == "dental"


def test_classify_fallback_without_key(monkeypatch):
    monkeypatch.setattr(social_brain, "GROQ_API_KEY", "")
    cls = social_brain.classify("nice work!", author="x")
    assert cls["category"] == social_brain.ENGAGE


def test_craft_reply_lead_register():
    client = FakeClient("Want a 2-min clip of it handling a real call?")
    out = social_brain.craft_reply("we miss half our calls",
                                   {"category": "lead", "niche": "dental"},
                                   client=client)
    assert "clip" in out.lower()


def test_craft_reply_fallback_without_key(monkeypatch):
    monkeypatch.setattr(social_brain, "GROQ_API_KEY", "")
    out = social_brain.craft_reply("hi", {"category": "lead"})
    assert out  # non-empty human fallback


# ── orchestrator: do_engage ──────────────────────────────────────────────────────
def test_do_engage_drafts_and_captures_lead(tmp_path, monkeypatch):
    import social_agent

    item = InboundItem(platform="fake", item_id="m1",
                       text="we miss half our calls, how much?",
                       author_handle="clinicowner", kind="dm")
    fake = FakeConnector(inbound=[item])

    # Force a deterministic lead classification + reply, no network.
    monkeypatch.setattr(social_agent.social_brain, "classify",
                        lambda text, author="", client=None: {
                            "category": "lead", "niche": "dental",
                            "intent": "high", "summary": "pricing"})
    monkeypatch.setattr(social_agent.social_brain, "craft_reply",
                        lambda text, cls, author="", client=None: "here is a clip")

    monkeypatch.setattr(social_agent, "load_connectors", lambda *a, **k: [fake])
    monkeypatch.setattr(social_agent, "SocialState",
                        lambda *a, **k: SocialState(tmp_path / "s.json"))
    monkeypatch.setattr(social_agent, "_DRAFTS", tmp_path / "drafts.jsonl")
    monkeypatch.setattr(social_agent, "_LEADS", tmp_path / "leads.jsonl")
    monkeypatch.setattr(social_agent, "_RUNS", tmp_path)
    # isolate the dashboard feed (tested separately)
    monkeypatch.setattr(command_center, "_FEED", tmp_path / "feed.json")

    stats = social_agent.do_engage(send=False)

    assert stats["leads"] == 1
    assert stats["drafted"] == 1
    assert not fake.replies                       # draft mode → nothing sent
    leads = (tmp_path / "leads.jsonl").read_text(encoding="utf-8").strip()
    assert "clinicowner" in leads
    drafts = (tmp_path / "drafts.jsonl").read_text(encoding="utf-8").strip()
    assert "here is a clip" in drafts


def test_do_engage_sends_when_enabled(tmp_path, monkeypatch):
    import social_agent

    item = InboundItem(platform="fake", item_id="m2", text="cool stuff",
                       author_handle="peer")
    fake = FakeConnector(inbound=[item])
    monkeypatch.setattr(social_agent.social_brain, "classify",
                        lambda text, author="", client=None: {"category": "engage"})
    monkeypatch.setattr(social_agent.social_brain, "craft_reply",
                        lambda text, cls, author="", client=None: "thanks! what are you building?")
    monkeypatch.setattr(social_agent, "load_connectors", lambda *a, **k: [fake])
    monkeypatch.setattr(social_agent, "SocialState",
                        lambda *a, **k: SocialState(tmp_path / "s.json"))
    monkeypatch.setattr(social_agent, "_RUNS", tmp_path)
    monkeypatch.setattr(command_center, "_FEED", tmp_path / "feed.json")

    stats = social_agent.do_engage(send=True)
    assert stats["replied"] == 1
    assert fake.replies and fake.replies[0][1].startswith("thanks")


# ── command center feed (the dashboard surface) ─────────────────────────────────
def test_redact_handle():
    assert command_center.redact_handle("drsharma") == "d••••••a"
    assert command_center.redact_handle("@bob") == "b•b"
    assert command_center.redact_handle("") == "anon"


def test_feed_publishes_post_draft_lead_with_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr(command_center, "_FEED", tmp_path / "feed.json")
    command_center.add_post("a real post about missed calls", series="teardown")
    command_center.add_draft(platform="console", author="drsharma", category="lead",
                             niche="dental", intent="high", draft="want a clip?")
    command_center.add_lead("console", "dental", "high")

    feed = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert feed["latest_post"]["text"].startswith("a real post")
    assert feed["stats"]["drafts_pending"] == 1
    assert feed["leads"]["by_niche"]["dental"] == 1
    # privacy: redacted handle present, raw handle absent
    assert feed["drafts"][0]["who"] == "d••••••a"
    blob = json.dumps(feed, ensure_ascii=False)
    assert "drsharma" not in blob


def test_command_center_connector_post_writes_feed(tmp_path, monkeypatch):
    from modules.connectors.command_center import CommandCenterConnector
    monkeypatch.setattr(command_center, "_FEED", tmp_path / "feed.json")
    c = CommandCenterConnector()
    assert c.available() and not c.can_read()
    res = c.post("hello dashboard")
    assert res.ok
    feed = json.loads((tmp_path / "feed.json").read_text(encoding="utf-8"))
    assert feed["latest_post"]["text"] == "hello dashboard"
