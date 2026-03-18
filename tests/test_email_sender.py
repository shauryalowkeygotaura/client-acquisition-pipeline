from modules.email_sender import build_message

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
