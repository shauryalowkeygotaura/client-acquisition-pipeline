"""Short, human clinic names for copy.

Scraped names carry listing junk: "Citadel Dental clinic-an oral cancer &
maxillofacial surgery", "Doctor Sanjay Kumar's Dental Clinic and Dental Studio".
Pasting that into an email is the loudest "a bot wrote this" signal we send.
A person writing by hand says "Citadel Dental".
"""
from __future__ import annotations

import re

# Anything after one of these is a tagline, a location or a service list.
_CUT = re.compile(r"\s*(?:[-|–—(:,/]|\s&\s|\band\b|\bby\b)\s*", re.I)
# Words that end a clinic's real name. We keep the word itself.
_ENDERS = ("clinic", "dental", "dentistry", "hospital", "physiotherapy", "physio",
           "centre", "center", "care", "studio", "eye", "eyecare", "smiles", "smile")
_PREFIX = re.compile(r"^(?:dr\.?|doctor)\s+", re.I)


def short_name(company: str, max_words: int = 3) -> str:
    """'Citadel Dental clinic-an oral cancer...' -> 'Citadel Dental'."""
    name = (company or "").strip()
    if not name:
        return "your clinic"
    head = _CUT.split(name, maxsplit=1)[0].strip() or name
    words = head.split()
    # Stop at the first "ender" word after the distinctive part, so
    # "Sharma Dental Clinic Jaipur" -> "Sharma Dental Clinic" -> trimmed below.
    for i, w in enumerate(words):
        if i >= 1 and w.lower().strip(".,'") in _ENDERS:
            words = words[: i + 1]
            break
    # A trailing generic "clinic" adds nothing a person would say.
    if len(words) > 2 and words[-1].lower() in ("clinic", "centre", "center", "hospital"):
        words = words[:-1]
    words = words[:max_words]
    out = " ".join(words).strip(" .,'")
    # Doctor-named clinics: "Doctor Sanjay Kumar's Dental" reads fine as "Dr Sanjay Kumar's".
    out = _PREFIX.sub("Dr ", out)
    return out or "your clinic"
