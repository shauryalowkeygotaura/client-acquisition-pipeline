from unittest.mock import patch
from modules.researcher import (
    extract_email_from_text,
    extract_emails_from_html,
    is_generic_mailbox,
)


def test_extract_email_mailto():
    html = '<a href="mailto:sarah@meridiandental.com">Contact</a>'
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_emails_from_html(html)
    assert "sarah@meridiandental.com" in emails


def test_extract_email_plain_text():
    text = "Contact us at sarah@acmecorp.com for more info"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert "sarah@acmecorp.com" in emails


def test_extract_email_none():
    text = "No email here, just text"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert emails == []


def test_extract_email_filters_generic_mailboxes():
    # Both noreply@ AND info@ are role mailboxes — neither should be returned.
    text = "noreply@company.com and info@company.com and sarah@company.com"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert "noreply@company.com" not in emails
    assert "info@company.com" not in emails
    assert "sarah@company.com" in emails


def test_is_generic_mailbox_known_prefixes():
    for addr in [
        "info@x.com", "hello@x.com", "hr@x.com", "careers@x.com",
        "noreply@x.com", "support@x.com", "sales@x.com", "admin@x.com",
    ]:
        assert is_generic_mailbox(addr), f"expected {addr} to be generic"


def test_is_generic_mailbox_personal_emails():
    for addr in ["sarah@x.com", "sarah.johnson@x.com", "s.johnson@x.com"]:
        assert not is_generic_mailbox(addr), f"expected {addr} to be personal"
