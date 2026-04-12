from modules.email_sender import build_message, _clean_email, send

def test_build_message_has_required_headers(sample_research):
    data = {**sample_research,
            "email_subject": "I built an AI receptionist for Meridian Dental",
            "email_body": "Hi Sarah, ..."}
    msg = build_message(data, from_addr="shaurya@gmail.com")
    assert msg["Subject"] == "I built an AI receptionist for Meridian Dental"
    assert msg["To"] == "info@meridiandental.com"
    assert msg["From"] == "shaurya@gmail.com"

def test_build_message_body_present(sample_research):
    data = {**sample_research,
            "email_subject": "Subj",
            "email_body": "Hi Sarah, test body"}
    msg = build_message(data, from_addr="shaurya@gmail.com")
    payload = msg.get_payload()
    assert "Hi Sarah" in str(payload)


# _clean_email validation
def test_clean_email_strips_whitespace():
    assert _clean_email("  info@example.com\n") == "info@example.com"

def test_clean_email_valid():
    assert _clean_email("contact@dentist.co.uk") == "contact@dentist.co.uk"

def test_clean_email_missing_at():
    assert _clean_email("notanemail.com") is None

def test_clean_email_missing_domain():
    assert _clean_email("user@") is None

def test_clean_email_html_artifact():
    assert _clean_email("info@domain") is None  # no TLD

def test_clean_email_empty():
    assert _clean_email("") is None

def test_clean_email_with_newline_embedded():
    assert _clean_email("info@doma\nin.com") is None


# send() returns (ok: bool, msg_id: str, sender: str) — check only the ok flag
def test_send_returns_false_for_invalid_email(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "apppass")
    ok, _msg_id, _sender = send({
        "email": "not-an-email",
        "email_subject": "Subj",
        "email_body": "Body",
    })
    assert ok is False

def test_send_returns_false_for_missing_email(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "apppass")
    ok, _msg_id, _sender = send({"email_subject": "Subj", "email_body": "Body"})
    assert ok is False

def test_send_returns_false_for_whitespace_only_email(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "apppass")
    ok, _msg_id, _sender = send({"email": "   ", "email_subject": "Subj", "email_body": "Body"})
    assert ok is False
