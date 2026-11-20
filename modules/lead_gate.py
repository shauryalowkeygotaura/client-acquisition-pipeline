"""Per-lead target gate: is this actually a clinic we should email?

Added 2026-11-20 after the sent folder showed first touches to agoda.com,
propertywala.com, niscpr.res.in (a government research institute) and
biyanicolleges.org. Every off-target send is a stranger who will never reply,
which is exactly the engagement signal that pushes a sender toward spam.

Whitelist over blacklist (vault rule): a lead passes only when something about
it positively says "clinic". The blocklist exists to catch obvious non-buyers
that happen to contain a clinic word ("Medical College", "Health Ministry").
"""
from __future__ import annotations

import re

# A lead must match one of these in its name, niche or domain to be emailed.
_CLINIC_WORDS = re.compile(
    r"dent|clinic|physio|ortho|eye|optic|optom|vision|skin|derma|aesthetic|"
    r"cosmet|hair|ivf|fertil|gyn|pediat|paediat|child\s*care|ent\b|"
    r"homeo|ayur|chiro|rehab|diagnost|patholog|medical|health|hospital|"
    r"nursing|maternity|kidney|cardio|neuro|smile|tooth|teeth|vet\b|veterin",
    re.I,
)

# Never a buyer, whatever else matches.
_BLOCK_WORDS = re.compile(
    r"college|university|school|institute|academy|council|ministry|"
    r"department|govt|government|municipal|corporation\s+of|research|"
    r"pharma\s+ltd|hotel|travel|realty|real\s*estate|propert",
    re.I,
)
_BLOCK_TLDS = (".gov.in", ".nic.in", ".res.in", ".edu", ".ac.in", ".edu.in", ".gov", ".mil")
_BLOCK_DOMAINS = {
    "agoda.com", "booking.com", "makemytrip.com", "propertywala.com", "99acres.com",
    "magicbricks.com", "justdial.com", "practo.com", "indiamart.com", "sulekha.com",
    "lybrate.com", "linkedin.com", "facebook.com", "instagram.com",
}


def _domain_of(lead: dict) -> str:
    email = (lead.get("email") or "").lower()
    if "@" in email:
        return email.rsplit("@", 1)[1]
    return (lead.get("domain") or "").lower().removeprefix("www.")


def check(lead: dict) -> tuple[bool, str]:
    """(ok, reason). ok=False means: do not contact this lead."""
    name = lead.get("company_name") or ""
    niche = lead.get("niche") or ""
    domain = _domain_of(lead)

    if domain in _BLOCK_DOMAINS or any(domain.endswith(t) for t in _BLOCK_TLDS):
        return False, f"blocked domain {domain}"
    if _BLOCK_WORDS.search(name):
        return False, f"non-buyer name: {name}"
    haystack = f"{name} {niche} {domain}"
    if not _CLINIC_WORDS.search(haystack):
        return False, f"no clinic signal in name/niche/domain: {name!r} ({niche})"
    return True, ""
