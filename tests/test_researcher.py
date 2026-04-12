from unittest.mock import patch
from modules.researcher import extract_email_from_text, extract_emails_from_html


def test_extract_email_mailto():
    html = '<a href="mailto:info@meridiandental.com">Contact</a>'
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_emails_from_html(html)
    assert "info@meridiandental.com" in emails


def test_extract_email_plain_text():
    text = "Contact us at hello@acmecorp.com for more info"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert "hello@acmecorp.com" in emails


def test_extract_email_none():
    text = "No email here, just text"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert emails == []


def test_extract_email_filters_noreply():
    # DNS check mocked — tests only the blocklist logic, not DNS
    text = "noreply@company.com and info@company.com"
    with patch("modules.researcher._is_valid_email_domain", return_value=True):
        emails = extract_email_from_text(text)
    assert "noreply@company.com" not in emails
    assert "info@company.com" in emails
