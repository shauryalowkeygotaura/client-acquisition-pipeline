import pytest
from modules.scraper import build_indeed_url, parse_slug, parse_domain

def test_build_indeed_url():
    url = build_indeed_url("New York, NY")
    assert "q=receptionist" in url
    assert "New+York" in url or "New%20York" in url
    assert "DSQF7" in url  # urgently hiring filter
    assert "fromage=1" in url

def test_parse_slug_basic():
    assert parse_slug("Meridian Dental LLC") == "meridian-dental-llc"

def test_parse_slug_special_chars():
    assert parse_slug("Dr. Smith's Clinic") == "dr-smiths-clinic"

def test_parse_domain_full_url():
    assert parse_domain("https://meridiandental.com/about") == "meridiandental.com"

def test_parse_domain_no_www():
    assert parse_domain("http://www.acmecorp.com") == "acmecorp.com"

def test_parse_domain_none():
    assert parse_domain(None) is None

def test_parse_domain_empty():
    assert parse_domain("") is None
