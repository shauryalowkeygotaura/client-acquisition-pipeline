"""Tests for the four-prong niche qualifier.

The whole point of this module is that it refuses to produce a confident green
out of guesses, so most of these tests are about what it does with MISSING
evidence rather than with good evidence.

Two real bugs are pinned here because both were live for a while:

  - `sellable_share_from_listings` read only `row["name"]`, but ranked lead
    rows carry `label`. Measured 2026-09-11 against runs/leads.json: 38 rows,
    zero with a `name` key, and the function returned a confident 100.0%
    computed over 38 empty strings.
  - prong 4 measured against runs/leads.json is circular. That file is
    POST-filter output, so it reads ~100% sellable by construction.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import niche_filter as nf  # noqa: E402


# --------------------------------------------------------------------------
# Prong 1: labor heavy

def test_labor_share_above_floor_passes():
    p = nf.labor_heavy(0.42)
    assert p.ok and "42%" in p.evidence


def test_labor_share_below_floor_fails():
    assert nf.labor_heavy(0.05).verdict == nf.FAIL


def test_labor_falls_back_to_staff_count():
    assert nf.labor_heavy(None, staff_count=4).ok
    assert nf.labor_heavy(None, staff_count=1).verdict == nf.FAIL


def test_labor_with_no_evidence_is_unknown():
    assert nf.labor_heavy().verdict == nf.UNKNOWN


def test_labor_rejects_a_percentage_passed_as_42():
    """0-1 fraction, not 0-100. 42 is not 42%, it is nonsense."""
    assert nf.labor_heavy(42).verdict == nf.UNKNOWN


# --------------------------------------------------------------------------
# Prong 2: fragmented

def test_fragmented_derives_share_from_counts():
    p = nf.fragmented(None, operators=1000, largest_operator_sites=30)
    assert p.ok and "3%" in p.evidence


def test_dominant_incumbent_fails():
    assert nf.fragmented(0.55).verdict == nf.FAIL


def test_fragmented_zero_operators_is_unknown_not_a_crash():
    assert nf.fragmented(None, operators=0, largest_operator_sites=0).verdict == nf.UNKNOWN


def test_fragmented_with_no_evidence_is_unknown():
    assert nf.fragmented().verdict == nf.UNKNOWN


# --------------------------------------------------------------------------
# Prong 3: small market (small is the PASS condition)

def test_small_market_passes_and_large_fails():
    assert nf.small_market(1_800).ok
    assert nf.small_market(250_000).verdict == nf.FAIL


def test_market_size_boundary_is_inclusive():
    assert nf.small_market(nf.MAX_MARKET_SIZE).ok
    assert nf.small_market(nf.MAX_MARKET_SIZE + 1).verdict == nf.FAIL


def test_empty_market_is_unknown_not_a_pass():
    """0 businesses would sail under the ceiling. It is not a market."""
    assert nf.small_market(0).verdict == nf.UNKNOWN


# --------------------------------------------------------------------------
# Prong 4: already spends / can actually buy

def test_sellable_share_gate():
    assert nf.already_spends(0.57).ok
    assert nf.already_spends(0.43).verdict == nf.FAIL


def test_delhi_medical_reproduces_the_measured_failure():
    """43% sellable is the real Delhi/Jaipur number. It must fail."""
    p = nf.already_spends(0.43)
    assert p.verdict == nf.FAIL
    assert "government" in p.evidence


def test_bool_evidence_accepted_when_no_number_exists():
    assert nf.already_spends(None, spends_on_marketing=True).ok
    assert nf.already_spends(None, spends_on_marketing=False).verdict == nf.FAIL


# --------------------------------------------------------------------------
# Whole-niche verdicts

def test_all_four_measured_and_passing_qualifies():
    v = nf.evaluate("Jaipur private dental",
                    labor_share=0.42, top_player_share=0.05,
                    business_count=1800, sellable_share=0.57)
    assert v.qualified and v.passed == 4
    assert "QUALIFIED" in v.summary()


def test_three_passes_and_one_unknown_is_not_qualified():
    """An unmeasured prong is not a near-miss, it is unmeasured."""
    v = nf.evaluate("half-researched niche",
                    labor_share=0.42, top_player_share=0.05, business_count=1800)
    assert not v.qualified
    assert v.unknowns == ["already_spends"]
    assert "UNMEASURED" in v.summary()


def test_a_niche_with_no_evidence_scores_zero_not_four():
    v = nf.evaluate("pure vibes")
    assert v.passed == 0 and len(v.unknowns) == 4
    assert not v.qualified


def test_delhi_medical_niche_fails_on_prong_four_alone():
    v = nf.evaluate("Delhi medical clinics (raw OSM)",
                    labor_share=0.40, top_player_share=0.03,
                    business_count=4200, sellable_share=0.43)
    assert not v.qualified
    assert v.failures == ["already_spends"]


def test_report_renders_every_prong():
    text = nf.report(nf.evaluate("x", labor_share=0.4))
    for prong in ("labor_heavy", "fragmented", "small_market", "already_spends"):
        assert prong in text


# --------------------------------------------------------------------------
# The listings bridge: the two traps

def test_sellable_share_reads_label_not_just_name():
    """Ranked lead rows carry `label`. Reading only `name` scored 38 blanks."""
    rows = [{"label": "Smile Dental Care"}, {"label": "CGHS Dispensary Rohini"}]
    share = nf.sellable_share_from_listings(rows)
    assert share is not None
    assert 0.0 <= share <= 1.0


def test_unnamed_rows_never_count_as_sellable():
    """A blank name classifies as private, which would be a free pass."""
    assert nf.sellable_share_from_listings([{"phone": "1"}, {"phone": "2"}]) is None


def test_mostly_unnamed_sample_returns_none():
    rows = [{"label": "Smile Dental"}] + [{"phone": str(i)} for i in range(4)]
    assert nf.sellable_share_from_listings(rows) is None


def test_empty_and_garbage_input():
    assert nf.sellable_share_from_listings([]) is None
    assert nf.sellable_share_from_listings(None) is None
    assert nf.sellable_share_from_listings(["not a dict", 42]) is None


def test_a_government_heavy_sample_drags_the_share_down():
    rows = [{"label": "CGHS Wellness Centre"},
            {"label": "ESI Dispensary"},
            {"label": "MCD Clinic Karol Bagh"},
            {"label": "Dr Sharma Dental Care"}]
    share = nf.sellable_share_from_listings(rows)
    assert share is not None
    assert share < nf.MIN_SELLABLE_SHARE, (
        "three of four are government bodies; this sample must fail prong 4")
