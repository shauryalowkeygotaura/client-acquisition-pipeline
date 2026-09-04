"""
lead_quality.py -- VENDORED from command-center/scripts/lead_quality.py.

Vendored rather than imported because these are separate repos, the same way
this vault vendors its shared Groq client module. command-center is the source of
truth: fix it there first, then copy the file across. Only the module docstring
below this banner differs.

WHY THE PIPELINE NEEDS IT: osm_scraper.OSM_NICHE_FILTERS queries
amenity=clinic, amenity=doctors and healthcare=clinic for the "medical" niche.
In Indian OSM that branch is largely public health infrastructure -- 43% of
Delhi's phone-bearing clinic nodes are CGHS, ESI, MCD or civil dispensaries.
Nothing downstream filtered them: scorer.py scores pain and adoption but never
asks whether the entity can buy at all, so government facilities were scored,
enriched, and written to runs/leads.json like any prospect.

"""

from __future__ import annotations

import re
from dataclasses import dataclass

GOVERNMENT = "government"
INSTITUTION = "institution"
CHAIN = "chain"
PRIVATE = "private"


@dataclass(frozen=True)
class Verdict:
    kind: str
    sellable: bool
    reason: str = ""


# Markers are deliberately narrow. A false positive here silently deletes a
# real prospect, which is worse than letting one dispensary through, so
# anything ambiguous in Indian clinic naming is left OUT:
#
#   "trust", "seva", "welfare", "society", "institute", "foundation", and
#   "college" on its own all appear in private brand names (Aggarwal Eye
#   Institute, Trust Dental Care), so none of them is used alone.
#
#   "hospital" is NOT a marker. "X Dental Hospital" is the standard name for a
#   private Indian dental practice: 20 of the 71 Jaipur listings call
#   themselves hospitals and every one is private. Blocking the word would
#   empty the pond.
_GOVERNMENT_PATTERNS = [
    # Central / state / municipal health schemes
    r"\bcghs\b", r"\besic?\b", r"employees.{0,3}state insurance",
    r"\bmcd\b", r"\bndmc\b", r"\bsdmc\b", r"\bedmc\b",
    r"\bmunicipal\b", r"nagar nigam", r"nagar palika", r"\bpanchayat\b",
    r"\bgovt\.?\b", r"\bgovernment\b", r"\bsarkari\b",
    r"\bjan aushadhi\b",
    # A "dispensary" in India is a public facility. Private practices call
    # themselves clinics. Across all 124 flagged rows in public/calls/, every
    # single "Dispensary"/"Dispensery" was government: CGHS, ESI, MCD, civil,
    # allopathic, unani, ayurvedic.
    r"\bdispensar(y|ies)\b", r"\bdispensery\b",
    r"\bcivil (dispensary|hospital)\b", r"\bdistrict hospital\b",
    r"\bprimary health\b", r"\bcommunity health\b", r"health sub.?cent",
    r"\bphc\b", r"\bchc\b", r"\buphc\b",
    r"\bmohalla clinic\b", r"\baam aadmi\b",
    # Uniformed and departmental services
    r"\brailway\b", r"\barmy\b", r"\bair ?force\b", r"\bnavy\b", r"\bnaval\b",
    r"\bmilitary\b", r"\bcantonment\b", r"\bcrpf\b", r"\bbsf\b", r"\bitbp\b",
    r"\bpolice\b", r"\bjail\b", r"\bprison\b", r"\bcoast guard\b",
    # Named public hospitals whose OPDs get tagged as clinics
    r"\baiims\b", r"\bsafdarjung\b", r"\blok nayak\b", r"\brml\b",
    r"ram manohar lohia", r"lady hardinge", r"hindu rao",
    r"guru teg ?bahadur", r"\bgtb hospital\b", r"deen dayal upadhyay",
    r"\bsms hospital\b",
]

_INSTITUTION_PATTERNS = [
    r"\bdental college\b", r"\bmedical college\b", r"college of dental",
    r"\buniversity\b", r"institute of medical sciences",
    r"\bcharitable\b", r"\bcharity\b", r"\bteaching hospital\b",
]

# Multi-city groups with central procurement. Kept, not dropped: see docstring.
_CHAIN_PATTERNS = [
    r"\bclove dental\b", r"\bclove\b", r"\bsabka dentist\b", r"\bapollo\b",
    r"\bfortis\b", r"\bmax (healthcare|hospital)\b", r"\bmanipal\b",
    r"\bnarayana health\b", r"\bpartha dental\b", r"\baxiss dental\b",
    r"\bdentzz\b", r"\bfms dental\b", r"\bindira ivf\b", r"\bdr\.? ?lal ?path\b",
    r"\bmetropolis\b", r"\bthyrocare\b", r"\bmedanta\b", r"\bcloudnine\b",
]

_GOVERNMENT_RE = re.compile("|".join(_GOVERNMENT_PATTERNS), re.I)
_INSTITUTION_RE = re.compile("|".join(_INSTITUTION_PATTERNS), re.I)
_CHAIN_RE = re.compile("|".join(_CHAIN_PATTERNS), re.I)


def classify(name: str, *, operator: str = "", operator_type: str = "") -> Verdict:
    """Judge one listing from its name and OSM operator tags.

    `operator_type` is OSM's own `operator:type`. Where present it is
    authoritative and beats the name, but it is set on well under 1% of Indian
    clinic nodes (1 of 109 Delhi dentists carried it), so the name does the work.
    """
    ot = (operator_type or "").strip().lower()
    if ot in ("government", "public"):
        return Verdict(GOVERNMENT, False, "operator:type=%s" % ot)

    hay = "%s %s" % (name or "", operator or "")

    m = _GOVERNMENT_RE.search(hay)
    if m:
        return Verdict(GOVERNMENT, False, "government marker %r" % m.group(0).strip())

    m = _INSTITUTION_RE.search(hay)
    if m:
        return Verdict(INSTITUTION, False, "institution marker %r" % m.group(0).strip())

    m = _CHAIN_RE.search(hay)
    if m:
        return Verdict(CHAIN, True, "chain %r" % m.group(0).strip())

    return Verdict(PRIVATE, True)


# --- Description -----------------------------------------------------------
# What a row has to say before you dial it: what kind of place it is, whether
# it already has a website (the free-chatbot opener only lands on clinics that
# do not), and any signal of size. Everything else is noise on a call sheet.

_TYPE_LABELS = {
    "dentist": "Dental clinic",
    "dental_clinic": "Dental clinic",
    "doctors": "Doctor's clinic",
    "clinic": "Clinic",
    "hospital": "Hospital",
    "orthodontist": "Orthodontist",
    "optometrist": "Optician",
    "physiotherapist": "Physiotherapist",
    "diagnostics": "Diagnostics lab",
    "homeopathy": "Homeopathy clinic",
    "ayurveda": "Ayurvedic clinic",
    "unani": "Unani clinic",
}


# Sources disagree on how much they tell you. OSM often has amenity/healthcare
# tags; SerpAPI has `type`; a row rescued from an older daily file has neither,
# only a name. Reading the type off the name is a last resort, but "Dental
# clinic - no website" is a usable call-sheet row and "no website" is not.
_NAME_TYPE_HINTS = [
    (r"orthodont", "orthodontist"),
    (r"dental|dentist|dento|\btooth\b|\bteeth\b|\bsmile", "dentist"),
    (r"physiotherap|\bphysio\b", "physiotherapist"),
    (r"optical|\boptom|optician|\beye\b|\bvision\b", "optometrist"),
    (r"diagnost|imaging|path ?lab|\bx.?ray\b", "diagnostics"),
    (r"homeo", "homeopathy"),
    (r"ayurved", "ayurveda"),
    (r"\bunani\b", "unani"),
    (r"\bhospital\b", "hospital"),
    (r"\bclinic\b|\bcentre\b|\bcenter\b|\bcare\b", "clinic"),
]
_NAME_TYPE_RES = [(re.compile(pat, re.I), label) for pat, label in _NAME_TYPE_HINTS]


def infer_type(name: str) -> str:
    """Best guess at what a place is, from its name alone. '' when unsure."""
    for rx, label in _NAME_TYPE_RES:
        if rx.search(name or ""):
            return label
    return ""


def _speciality(raw: str) -> str:
    """OSM healthcare:speciality is a semicolon-separated list of tokens."""
    if not raw:
        return ""
    parts = [p.strip().replace("_", " ") for p in re.split(r"[;,]", raw) if p.strip()]
    return ", ".join(parts[:2])


def describe(listing: dict, verdict: Verdict | None = None) -> str:
    """The one line shown under the number in the CALL panel."""
    bits: list[str] = []

    kind = (listing.get("business_type") or listing.get("type") or "").strip().lower()
    if not kind:
        kind = infer_type(listing.get("title") or listing.get("label") or "")
    label = _TYPE_LABELS.get(kind, kind.replace("_", " ").title() if kind else "")
    if verdict and verdict.kind == CHAIN:
        label = (label + " (chain)").strip() if label else "Chain"
    if label:
        bits.append(label)

    spec = _speciality(listing.get("speciality", ""))
    if spec:
        bits.append(spec)

    rating = str(listing.get("rating") or "").strip()
    reviews = str(listing.get("reviews") or "").strip()
    if rating:
        bits.append(rating + "*" + (" (%s)" % reviews if reviews else ""))

    bits.append("has website" if (listing.get("website") or "").strip() else "no website")

    hours = (listing.get("hours") or "").strip()
    if hours and len(hours) <= 24:
        bits.append(hours)

    return " · ".join(b for b in bits if b)


# --- Ranking ---------------------------------------------------------------
# Fifty calls a day, worked top to bottom, and the day usually runs out before
# the list does. So the order is the product.
#
# WHAT IS DEFENSIBLE AND WHAT IS A GUESS - read this before trusting a number.
#
# The TIER is definitional, not predictive. It asks the only two questions that
# have to be true before a call can possibly work:
#     can this call reach someone with the authority to say yes?
#     does the pitch land on this kind of business?
# A is both, B is one, C is neither. That is the same test that removed the
# government listings: a civil dispensary fails both, so it is not a lead.
#
# The SCORE only breaks ties inside a tier, and its weights are STATED PRIORS,
# not measured ones. Nothing here has been checked against call outcomes,
# because no call outcome is recorded anywhere yet - the panel stores a `called`
# boolean and nothing else. Treat the score as an argument, which is why every
# row carries the reasons that produced it. What would make it real: record per
# call whether it reached a decision-maker and whether it went anywhere, then
# compare tiers. Until then, ordering is a hypothesis.
#
# The two priors that ARE grounded in something, from the only two clinics that
# have actually bought (assistant/clinics/*_CLIENT.md in dental-receptionist):
#   - Both had a website, one with an online booking page. So a website is a
#     POSITIVE adoption signal here. The old acquisition README says to
#     prioritise clinics with no website; that belonged to the earlier free
#     website-chatbot offer, and is stale for a phone product.
#   - Both are Jaipur, and one carries 1,150+ Google reviews. A busy practice
#     misses more calls, which is the entire pitch.
# n=2 cannot set a weight. It can only stop the ranking from putting known
# customers at the bottom, which is the check test_ranking_matches_real_clients
# makes.

TIER_A, TIER_B, TIER_C = "A", "B", "C"

# An eponymous clinic ("Dr. Dubey's Dental Clinic") is almost always owner-run,
# so the number reaches one person who can decide. Unvalidated prior: neither
# paying client has an eponymous name, so this is reasoning, not evidence.
_EPONYMOUS_RE = re.compile(r"\b(dr\.?|doctor)\b|['’]s\b", re.I)

# Cities where the pitch has a local hook. Delhi: the DPS RK Puram student
# framing is local. Jaipur: home base, and two referenceable clinics in the
# same neighbourhood. Everywhere else the opener is a stranger from out of town.
_LOCAL_CITIES = ("delhi", "jaipur")

_DENTAL_TYPES = ("dentist", "dental_clinic", "orthodontist")
_MEDICAL_TYPES = ("clinic", "doctors", "hospital", "homeopathy", "ayurveda", "unani")


def _has_lunch_gap(hours: str) -> bool:
    """True when a day is split into two shifts, e.g. '09:30-14:00,17:30-20:00'.

    That gap is a window where the phone rings and nobody is there, which is
    one of the cases positioning.md names outright ("lunch-hour and break-time
    backup"). Marudhar Dental, a paying client, runs exactly this pattern.
    """
    if not hours:
        return False
    # Two time ranges joined by a comma inside one day's rule.
    return bool(re.search(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*,\s*\d{1,2}:\d{2}", hours))


def _as_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def rank(listing: dict, verdict: Verdict | None = None) -> dict:
    """Score one lead. Returns {tier, score, reasons}.

    `listing` is a daily-file row or a scraper item. Missing fields are never
    penalised - a source that did not supply reviews is not evidence of an
    unpopular clinic - so every component is a bonus, never a deduction, with
    the single exception of a chain, which is a fact about who can buy.
    """
    if verdict is None:
        verdict = classify(
            listing.get("label") or listing.get("title") or "",
            operator=listing.get("operator", ""),
            operator_type=listing.get("operator_type", ""),
        )

    name = listing.get("label") or listing.get("title") or ""
    reasons: list[str] = []
    score = 0

    # --- Reach: can the call land on someone who can say yes? --------------
    # A mobile is usually the dentist's own; a landline is the front desk,
    # which is both the wrong authority and the role the product touches.
    mobile = bool((listing.get("whatsapp") or "").strip())
    if not mobile and listing.get("number"):
        mobile = bool(_whatsapp_digits(listing["number"]))
    is_chain = verdict.kind == CHAIN

    if is_chain:
        score -= 4
        reasons.append("chain, buys centrally")
    elif mobile:
        score += 3
        reasons.append("mobile, likely the owner")
    else:
        reasons.append("landline, likely the front desk")

    if not is_chain and _EPONYMOUS_RE.search(name):
        score += 1
        reasons.append("owner-named")

    # --- Fit: does the pitch land on this kind of business? ----------------
    biz = (listing.get("business_type") or listing.get("type") or "").strip().lower()
    if not biz:
        biz = infer_type(name)
    spec = (listing.get("speciality") or "").lower()
    is_dental = biz in _DENTAL_TYPES or "dent" in spec or infer_type(name) == "dentist"

    if is_dental:
        score += 3
        reasons.append("dental, the pitch has proof")
    elif biz in _MEDICAL_TYPES:
        score += 1
        reasons.append("clinic, proof does not transfer")

    # --- Pain and adoption, bonus-only ------------------------------------
    reviews = _as_int(listing.get("reviews"))
    if reviews >= 50:
        score += 2
        reasons.append("%d reviews, busy" % reviews)
    elif reviews >= 10:
        score += 1
        reasons.append("%d reviews" % reviews)

    if (listing.get("website") or "").strip():
        score += 1
        reasons.append("has a website")

    if _has_lunch_gap(listing.get("hours") or ""):
        score += 1
        reasons.append("shuts mid-day, the gap the pitch names")

    area = (listing.get("area") or listing.get("address") or "").lower()
    for city in _LOCAL_CITIES:
        if city in area:
            score += 2
            reasons.append("%s, local angle" % city.title())
            break

    # --- Tier --------------------------------------------------------------
    reachable = mobile and not is_chain
    if reachable and is_dental:
        tier = TIER_A
    elif reachable or is_dental:
        tier = TIER_B
    else:
        tier = TIER_C

    return {"tier": tier, "score": score, "reasons": reasons}


# STD (landline) codes for every city build_call_list targets, minus the
# leading trunk 0. Only the ones starting 6-8 actually matter - see below - but
# the whole set is here so adding a city means adding its code, not rederiving
# the rule.
#
# WHY THIS LIST EXISTS. "0731-2551733" is an Indore landline. Stripped of the
# trunk 0 it is "7312551733", which is indistinguishable by shape from a mobile
# in the 73xx series, so the old rule "0 + ten digits starting 6-9 is a mobile"
# handed it a wa.me link. 49 of 395 rows in public/calls were like that: Indore
# 0731/0712, Bangalore 080, Ahmedabad 079, Patna 0612. Messaging those reaches
# a stranger, and cold WhatsApp to strangers is exactly what gets the personal
# number banned - the one risk public/calls/README.md calls out by name.
#
# Losing a wa button on a real mobile costs nothing, since the number is still
# callable. Messaging a stranger costs the account. So this errs toward
# landline, deliberately.
_STD_CODES = (
    "011",   # Delhi
    "020",   # Pune
    "022",   # Mumbai
    "033",   # Kolkata
    "040",   # Hyderabad
    "044",   # Chennai
    "079",   # Ahmedabad
    "080",   # Bangalore
    "0141",  # Jaipur
    "0161",  # Ludhiana
    "0172",  # Chandigarh
    "0253",  # Nashik
    "0261",  # Surat
    "0265",  # Vadodara
    "0422",  # Coimbatore
    "0484",  # Kochi
    "0512",  # Kanpur
    "0522",  # Lucknow
    "0562",  # Agra
    "0612",  # Patna
    "0712",  # Nagpur
    "0731",  # Indore
    "0755",  # Bhopal
)
# Compared against the number AFTER the trunk 0 is stripped.
_STD_PREFIXES = tuple(code[1:] for code in _STD_CODES if code.startswith("0"))


def whatsapp_digits(phone: str) -> str:
    """Indian mobile -> 91XXXXXXXXXX. '' for landlines and anything odd.

    The single definition of "is this number messageable", used by the
    generator, by rank() (which must score hand-pasted rows the generator never
    saw), and mirrored in components/CallList.tsx. If those drift, the [wa]
    button and the ranking disagree about the same number.
    """
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return d
    if len(d) == 10 and d[0] in "6789":
        return "91" + d
    if len(d) == 11 and d.startswith("0") and d[1] in "6789":
        rest = d[1:]
        # A known STD code here means the trunk 0 was dialling a landline, not
        # prefixing a mobile. Only 6/7/8-leading codes can reach this line at
        # all: no Indian STD code starts with 9, and 1-5 already failed above.
        if rest.startswith(_STD_PREFIXES):
            return ""
        return "91" + rest
    return ""


# Kept so existing call sites and tests keep working.
_whatsapp_digits = whatsapp_digits


def sort_key(row: dict):
    """Order for the call sheet: tier first, then score, best at the top.

    Tier leads because it is the part that is actually defensible; the score
    only arranges rows that pass or fail the same two tests. Python's sort is
    stable, so equal rows keep the order the source produced.
    """
    tier = row.get("tier") or TIER_C
    return ({"A": 0, "B": 1, "C": 2}.get(tier, 2), -_as_int(row.get("score")))
