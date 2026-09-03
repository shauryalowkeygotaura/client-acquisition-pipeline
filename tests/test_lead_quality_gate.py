"""The sellability gate on both scrapers.

Neither scraper asked whether a listing could buy. osm_scraper's "medical"
niche queries amenity=clinic, amenity=doctors and healthcare=clinic, and in
Indian OSM that branch is largely public health infrastructure: 43% of Delhi's
phone-bearing clinic nodes are CGHS, ESI, MCD or civil dispensaries. They were
scored, enriched and written to runs/leads.json like any prospect, because
scorer.py measures pain and adoption but never authority to buy.

    python -m pytest tests/test_lead_quality_gate.py -q
"""
import pytest

from modules.lead_quality import classify
from modules.maps_scraper import _to_lead as maps_lead
from modules.osm_scraper import _to_lead as osm_lead

UNSELLABLE = [
    "CGHS Dispensary Inderpuri",
    "CGHS Wellness Centre Lodhi Road II",
    "ESI Dispensary Factory Road",
    "MCD Dispensary, Mundka",
    "New Delhi Municipal Council Polyclinic",
    "Delhi Govt Dispensary, Jharoda Majra",
    "Civil Dispensary - Dhanas",
    "Aam Aadmi Polyclinic Tilak Vihar",
    "Government hospital, Madangir",
    "R D Jindal Charitable Clinic",
]

# Private practices whose names contain words a blunter filter would eat.
SELLABLE = [
    "Elegance Dental Clinic",
    "Shri Ganesh Dental Hospital, Jaipur",
    "Aggarwal Eye Institute",
    "Dr Rasika's Dental Wellness Centre",
    "Amol Liver and Gastro Hospital",
    "Centre For Sight",
]


def _osm(name, amenity="clinic"):
    return {"tags": {"name": name, "amenity": amenity, "phone": "9009822818"}}


@pytest.mark.parametrize("name", UNSELLABLE)
def test_osm_drops_what_cannot_buy(name):
    assert osm_lead(_osm(name), "Delhi", "medical") is None


@pytest.mark.parametrize("name", SELLABLE)
def test_osm_keeps_real_businesses(name):
    lead = osm_lead(_osm(name), "Delhi", "medical")
    assert lead is not None, f"{name} was dropped"
    assert lead["company_name"] == name


@pytest.mark.parametrize("name", UNSELLABLE)
def test_maps_drops_what_cannot_buy(name):
    assert maps_lead({"title": name, "phone": "9009822818"}, "Delhi", "medical") is None


@pytest.mark.parametrize("name", SELLABLE)
def test_maps_keeps_real_businesses(name):
    lead = maps_lead({"title": name, "phone": "9009822818"}, "Delhi", "medical")
    assert lead is not None, f"{name} was dropped"


def test_osm_honours_the_operator_type_tag():
    """OSM's own ownership tag beats the name where it is present, which is
    rarely: 1 of 109 Delhi dentist nodes carried it."""
    el = {"tags": {"name": "Sector 9 Clinic", "amenity": "clinic",
                   "phone": "9009822818", "operator:type": "government"}}
    assert osm_lead(el, "Delhi", "medical") is None


def test_an_unnamed_element_is_still_dropped():
    assert osm_lead({"tags": {"amenity": "dentist"}}, "Delhi", "dental") is None


def test_a_chain_is_kept_and_labelled():
    """Chains buy centrally, which is a judgement about the offer rather than a
    fact about the entity, so the pipeline keeps them."""
    v = classify("Clove Dental")
    assert v.kind == "chain" and v.sellable is True
    assert osm_lead(_osm("Clove Dental", "dentist"), "Delhi", "dental") is not None


def test_the_vendored_copy_has_not_drifted():
    """command-center is the source of truth; this file is a copy.

    A silent divergence means the dashboard's two panels disagree about which
    listings are leads. Compares everything after the module docstring.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]   # client-acquisition-pipeline
    here = repo / "modules" / "lead_quality.py"
    source = repo.parent / "command-center" / "scripts" / "lead_quality.py"
    if not source.exists():          # command-center not checked out beside us
        pytest.skip("command-center repo not present")

    def body(text):
        start = text.index('"""')
        return text[text.index('"""', start + 3) + 3:].strip()

    assert body(here.read_text(encoding="utf-8")) == body(
        source.read_text(encoding="utf-8")
    ), "modules/lead_quality.py has drifted from command-center/scripts/lead_quality.py"
