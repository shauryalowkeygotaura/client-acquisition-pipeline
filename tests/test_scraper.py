from modules.scraper import parse_slug, parse_domain, extract_website_from_text


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


def test_extract_website_skips_indeed():
    text = "Apply at https://indeed.com/job/123 or visit https://acmedental.com"
    assert extract_website_from_text(text) == "https://acmedental.com"


def test_extract_website_none():
    assert extract_website_from_text("No links here") is None
