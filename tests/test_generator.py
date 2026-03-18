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

def test_parse_output_valid():
    raw = json.dumps({
        "vapi_prompt": "You are the receptionist for Meridian Dental...",
        "email_subject": "I built an AI receptionist for Meridian Dental",
        "email_body": "Hi Sarah, I noticed...",
        "linkedin_msg": "Hey Sarah — saw Meridian Dental needs..."
    })
    result = parse_output(raw)
    assert result["vapi_prompt"].startswith("You are")
    assert "Meridian Dental" in result["email_subject"]

def test_parse_output_json_in_markdown():
    raw = '```json\n{"vapi_prompt": "X", "email_subject": "Y", "email_body": "Z", "linkedin_msg": "W"}\n```'
    result = parse_output(raw)
    assert result["vapi_prompt"] == "X"

def test_parse_output_missing_field_raises():
    raw = json.dumps({"vapi_prompt": "X", "email_subject": "Y"})
    with pytest.raises(GeneratorError):
        parse_output(raw)
