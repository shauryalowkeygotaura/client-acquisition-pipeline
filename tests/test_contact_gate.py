"""A lead nothing can contact is not a lead.

Measured on the committed run history, 2026-06-04 to 2026-09-03, 69 scrape runs:

    found    15010
    saved      225
    emailed      5
    whatsapp     0
    instagram    0
    linkedin     0

Every run was green. The summary line said "232 found, 38 new, 0 emailed,
0 WhatsApp, 0 IG, 0 LinkedIn" and gave no reason, so three months passed
without the funnel's own numbers saying anything was wrong.

runs/leads.json from 2026-09-03 explains it. All 38 saved leads scored exactly
5, none had a website, none had a phone, and every channel flag was false. They
were unreachable by construction: at a score of 5 the only channel offered is
email, email needs an address, an address needs a domain, and a domain needs a
website none of them had. Each one still cost a researcher call, an enricher
call, a scorer call and a generator call to produce a row that could never be
actioned.

This gate refuses them at the door. It does NOT touch the send tiers - which
channel fires at which score is a decision with real-world consequences (cold
WhatsApp volume gets a number banned) and belongs to Shaurya, not to a filter.

    python -m pytest tests/test_contact_gate.py -q
"""
import pytest

from pipeline import has_contact_method


@pytest.mark.parametrize("job", [
    {"company_name": "Shroff's Dental Clinic"},
    {"company_name": "Belle 32", "email": "", "phone": "", "company_website": ""},
    {"company_name": "My Dentist", "phone": None},
    {},
])
def test_a_lead_with_no_route_in_is_refused(job):
    assert has_contact_method(job) is False


@pytest.mark.parametrize("job", [
    {"company_name": "A", "phone": "+91 96361 80333"},
    {"company_name": "B", "email": "hi@clinic.in"},
    # A website is a route in because email_finder resolves the domain.
    {"company_name": "C", "company_website": "https://clinic.in"},
    {"company_name": "D", "phone": "011-26499400", "company_website": "https://x.in"},
])
def test_any_single_route_in_is_enough(job):
    assert has_contact_method(job) is True


def test_the_exact_shape_that_filled_the_funnel():
    """Reproduced from runs/leads.json, 2026-09-03. All 38 looked like this."""
    job = {"company_name": "Dental Centre", "slug": "dental-centre",
           "maps_niche": "dental", "business_type": "dentist",
           "address": "", "phone": "", "company_website": None, "domain": None}
    assert has_contact_method(job) is False


def test_a_website_of_only_whitespace_is_not_a_route_in():
    assert has_contact_method({"company_website": "   "}) is False
