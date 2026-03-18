# tests/conftest.py
import pytest

@pytest.fixture
def sample_job():
    return {
        "company_name": "Meridian Dental",
        "job_title": "Receptionist",
        "location": "New York, NY",
        "company_website": "https://meridiandental.com",
        "poster_name": "Sarah Johnson",
        "date_posted": "2026-03-18",
        "job_description_text": "Urgently hiring a receptionist for busy dental office.",
        "slug": "meridian-dental",
        "domain": "meridiandental.com",
    }

@pytest.fixture
def sample_research():
    return {
        "company_name": "Meridian Dental",
        "address": "123 Main St, New York, NY",
        "phone": "212-555-0100",
        "services": "general dentistry, cleanings, fillings, cosmetic dentistry",
        "hours": "Mon-Fri 9am-5pm",
        "email": "info@meridiandental.com",
        "linkedin_url": "https://linkedin.com/in/sarah-johnson-meridian",
        "scraped_details": "We offer gentle, affordable dental care.",
        "tone": "professional",
    }
