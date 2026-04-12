import json
import pytest
from unittest.mock import patch, MagicMock
from modules.generator import build_prompt, parse_output, GeneratorError

def test_build_prompt_contains_company(sample_research):
    prompt = build_prompt(sample_research)
    assert "Meridian Dental" in prompt
    assert "vapi_prompt" in prompt
    assert "email_body" in prompt
    assert "linkedin_msg" in prompt

_FULL_OUTPUT = {
    "vapi_prompt": "You are the receptionist for Meridian Dental...",
    "email_subject": "front desk coverage",
    "email_body_pain": "Pain body.",
    "email_body_curiosity": "Curiosity body.",
    "email_body_roi": "ROI body.",
    "email_body_question": "Are you still looking to fill that receptionist gap?\n\nI build voice agents for dental businesses — they pick up, answer FAQs, and book via cal.com.\n\nHappy to send a 2-min clip if useful.\n\n— Shaurya",
    "linkedin_msg": "Hey Sarah — saw Meridian Dental needs...",
    "linkedin_post": "Post text here.",
}

def test_parse_output_valid():
    raw = json.dumps(_FULL_OUTPUT)
    result = parse_output(raw)
    assert result["vapi_prompt"].startswith("You are")
    assert result["email_subject"] == "front desk coverage"

def test_parse_output_json_in_markdown():
    raw = f'```json\n{json.dumps(_FULL_OUTPUT)}\n```'
    result = parse_output(raw)
    assert result["vapi_prompt"].startswith("You are")

def test_parse_output_missing_field_raises():
    raw = json.dumps({"vapi_prompt": "X", "email_subject": "Y"})
    with pytest.raises(GeneratorError):
        parse_output(raw)
