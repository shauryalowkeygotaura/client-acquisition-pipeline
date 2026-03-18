from modules.sheets_writer import domain_exists, build_row, HEADERS

def test_headers_complete():
    required = {
        "slug", "company_name", "website", "domain", "contact_name",
        "email", "linkedin_url", "vapi_prompt", "email_subject",
        "email_body", "linkedin_msg", "email_sent", "linkedin_sent",
        "status", "sent_at", "replied_at", "vapi_assistant_id"
    }
    assert required.issubset(set(HEADERS))

def test_build_row_all_fields(sample_research):
    data = {**sample_research, "slug": "meridian-dental",
            "company_website": "https://meridiandental.com",
            "domain": "meridiandental.com",
            "vapi_prompt": "You are...", "email_subject": "Subj",
            "email_body": "Body", "linkedin_msg": "Msg"}
    row = build_row(data)
    assert len(row) == len(HEADERS)
    assert row[HEADERS.index("slug")] == "meridian-dental"
    assert row[HEADERS.index("status")] == "pending"
    assert row[HEADERS.index("email_sent")] == "FALSE"

def test_domain_exists_true():
    existing = [{"domain": "meridiandental.com"}, {"domain": "acme.com"}]
    assert domain_exists("meridiandental.com", existing) is True

def test_domain_exists_false():
    existing = [{"domain": "acme.com"}]
    assert domain_exists("meridiandental.com", existing) is False

def test_domain_exists_case_insensitive():
    existing = [{"domain": "MeridianDental.com"}]
    assert domain_exists("meridiandental.com", existing) is True
