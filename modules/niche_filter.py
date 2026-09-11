"""
niche_filter.py -- the four-prong niche qualifier, at market altitude.

lead_quality.classify() asks of ONE listing: can this entity buy at all? This
module asks the question one level up: should we be selling into this market in
the first place? Both are needed. A perfect private clinic inside a market with
40,000 competitors and a dominant incumbent is still a bad place to spend a
year.

The four prongs (source: @brodyautomates, "How I Built a $1.5M/year AI
Automation Agency", transcript in Research/YouTube/):

  1. LABOR HEAVY        they pay a lot of humans, so there are gaps to fill
  2. FRAGMENTED         no dominant player, many small operators, nobody serving
  3. SMALL (<~30k)      too small for traditional software to build for, which
                        is exactly why competition is thin
  4. ALREADY SPENDS     owners buy marketing / staff / consulting, so they
                        understand leverage and will pay for ROI

Prong 4 is the one this vault learned the hard way rather than from a video.
The Delhi/Jaipur scrape found 43% of phone-bearing clinic nodes were CGHS, ESI,
MCD or civil dispensaries: entities that cannot purchase regardless of how much
ROI you show them. lead_quality.py filters those per listing. Prong 4 is the
same fact expressed as a market property, so a niche that is MOSTLY such
entities fails before anyone scrapes it.

EVIDENCE OR NOTHING. Every prong takes a measured number. A prong with no
evidence returns UNKNOWN and can never count as a pass, because the failure
mode this guards against is a confident green built out of guesses. A niche
scored on two real numbers and two assumptions is not a qualified niche.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# "Under 30,000 businesses" as stated. Treated as a soft ceiling: the logic is
# "too small for a software company to bother targeting", not a magic number.
MAX_MARKET_SIZE = 30_000

# Share of the market held by the single largest player. Above this the market
# has an incumbent and is not fragmented in the sense that matters.
MAX_TOP_PLAYER_SHARE = 0.20

# Fraction of revenue going to human labour. Below this there is no staff cost
# to displace and the pitch has no arithmetic behind it.
MIN_LABOR_SHARE = 0.20

# Fraction of the market that can actually purchase (not government, not a
# captive chain outlet). Below this the pond is mostly unsellable.
MIN_SELLABLE_SHARE = 0.50


@dataclass(frozen=True)
class Prong:
    name: str
    verdict: str
    evidence: str

    @property
    def ok(self) -> bool:
        return self.verdict == PASS


@dataclass
class NicheVerdict:
    niche: str
    prongs: list[Prong] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for p in self.prongs if p.ok)

    @property
    def unknowns(self) -> list[str]:
        return [p.name for p in self.prongs if p.verdict == UNKNOWN]

    @property
    def failures(self) -> list[str]:
        return [p.name for p in self.prongs if p.verdict == FAIL]

    @property
    def qualified(self) -> bool:
        """All four prongs measured and passing. Nothing softer counts.

        The claim is that the four TOGETHER are what make a niche work, so a
        3/4 with one unknown is not "nearly qualified", it is unmeasured.
        """
        return len(self.prongs) == 4 and self.passed == 4

    def summary(self) -> str:
        if self.qualified:
            return "QUALIFIED - all four prongs measured and passing"
        bits = []
        if self.failures:
            bits.append("fails " + ", ".join(self.failures))
        if self.unknowns:
            bits.append("UNMEASURED: " + ", ".join(self.unknowns))
        if not bits:
            # Reachable only for a hand-built verdict that does not carry
            # exactly four prongs. Say that, rather than printing a bare
            # "NOT QUALIFIED (4/4)" that reads as a contradiction.
            return ("NOT QUALIFIED - {} prongs supplied, expected exactly 4"
                    .format(len(self.prongs)))
        return "NOT QUALIFIED ({}/4) - ".format(self.passed) + "; ".join(bits)


def _num(value) -> float | None:
    """Accept a real number, reject bools and anything unparseable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def labor_heavy(labor_share=None, *, staff_count=None) -> Prong:
    """Prong 1. Prefers a measured labour share of revenue; falls back to headcount."""
    share = _num(labor_share)
    if share is not None:
        if not 0.0 <= share <= 1.0:
            return Prong("labor_heavy", UNKNOWN,
                         "labor_share={!r} is not a fraction between 0 and 1".format(share))
        ok = share >= MIN_LABOR_SHARE
        return Prong("labor_heavy", PASS if ok else FAIL,
                     "labour is {:.0%} of revenue (need >= {:.0%})".format(share, MIN_LABOR_SHARE))
    count = _num(staff_count)
    if count is not None:
        ok = count >= 2
        return Prong("labor_heavy", PASS if ok else FAIL,
                     "{:.0f} staff per site (need >= 2 for a front desk to exist)".format(count))
    return Prong("labor_heavy", UNKNOWN, "no labour share or staff count supplied")


def fragmented(top_player_share=None, *, operators=None, largest_operator_sites=None) -> Prong:
    """Prong 2. Either a stated top-player share, or derive it from site counts."""
    share = _num(top_player_share)
    if share is None:
        total, biggest = _num(operators), _num(largest_operator_sites)
        if total is not None and total > 0 and biggest is not None:
            share = biggest / total
    if share is None:
        return Prong("fragmented", UNKNOWN, "no top-player share or operator counts supplied")
    if not 0.0 <= share <= 1.0:
        return Prong("fragmented", UNKNOWN,
                     "top-player share {!r} is not a fraction".format(share))
    ok = share <= MAX_TOP_PLAYER_SHARE
    return Prong("fragmented", PASS if ok else FAIL,
                 "largest player holds {:.0%} (need <= {:.0%})".format(share, MAX_TOP_PLAYER_SHARE))


def small_market(business_count=None) -> Prong:
    """Prong 3. Counterintuitive by design: SMALL is the pass condition."""
    n = _num(business_count)
    if n is None:
        return Prong("small_market", UNKNOWN, "no business count supplied")
    if n <= 0:
        return Prong("small_market", UNKNOWN,
                     "business_count={:.0f} is not a real market".format(n))
    ok = n <= MAX_MARKET_SIZE
    return Prong("small_market", PASS if ok else FAIL,
                 "{:,.0f} businesses (need <= {:,} so software firms skip it)".format(
                     n, MAX_MARKET_SIZE))


def already_spends(sellable_share=None, *, spends_on_marketing=None) -> Prong:
    """Prong 4. The prong this vault measured before it read the framework.

    `sellable_share` is the fraction of the market that can purchase at all.
    Feed it straight from a lead_quality sweep: sellable listings / total.
    """
    share = _num(sellable_share)
    if share is not None:
        if not 0.0 <= share <= 1.0:
            return Prong("already_spends", UNKNOWN,
                         "sellable_share {!r} is not a fraction".format(share))
        ok = share >= MIN_SELLABLE_SHARE
        return Prong("already_spends", PASS if ok else FAIL,
                     "{:.0%} of the market can actually purchase (need >= {:.0%}); the rest "
                     "is government or captive-chain and will never buy at any ROI".format(
                         share, MIN_SELLABLE_SHARE))
    if isinstance(spends_on_marketing, bool):
        return Prong("already_spends", PASS if spends_on_marketing else FAIL,
                     "owners observed buying marketing/staff/consulting"
                     if spends_on_marketing else
                     "no evidence owners buy outside services; cheap owners never convert")
    return Prong("already_spends", UNKNOWN, "no sellable share or spending evidence supplied")


def evaluate(niche: str, **evidence) -> NicheVerdict:
    """Score a niche against all four prongs.

    Accepts any subset of: labor_share, staff_count, top_player_share,
    operators, largest_operator_sites, business_count, sellable_share,
    spends_on_marketing. Whatever is missing comes back UNKNOWN rather than
    being assumed either way.
    """
    return NicheVerdict(niche=niche, prongs=[
        labor_heavy(evidence.get("labor_share"),
                    staff_count=evidence.get("staff_count")),
        fragmented(evidence.get("top_player_share"),
                   operators=evidence.get("operators"),
                   largest_operator_sites=evidence.get("largest_operator_sites")),
        small_market(evidence.get("business_count")),
        already_spends(evidence.get("sellable_share"),
                       spends_on_marketing=evidence.get("spends_on_marketing")),
    ])


# Listing dicts are not uniform across scrapers: osm_scraper writes `name`,
# the ranked lead rows write `label`. Reading only one of them silently scores
# every row as an empty string, which classifies as private and returns a
# confident 100%. Measured 2026-09-11 against runs/leads.json: 38 rows, none
# with a `name` key, share reported as 100.0%. That number was fiction.
_NAME_KEYS = ("name", "label", "title", "business_name")

# Below this fraction of rows carrying a usable name, the sample is not worth
# a number at all.
_MIN_NAMED_SHARE = 0.80


def _listing_name(row: dict) -> str:
    for key in _NAME_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sellable_share_from_listings(listings) -> float | None:
    """Derive prong 4's evidence from a real scrape, via lead_quality.

    This is the bridge that makes the filter measured rather than argued.

    TWO traps, both hit in practice:

    1. MEASURE THE RAW SCRAPE, NOT runs/leads.json. The leads file is
       POST-filter output that lead_quality has already cleaned, so it reads
       ~100% sellable by construction and prong 4 passes every time. Point this
       at the scraper's raw output instead.
    2. Rows must carry a name. A row whose name key is missing classifies as an
       empty string, which reads as private, which reads as sellable. Rows
       without a usable name are skipped, and if too few remain this returns
       None rather than a number built on blanks.
    """
    try:
        from . import lead_quality
    except ImportError:            # vendored / flat layout
        import lead_quality        # type: ignore

    rows = [r for r in (listings or []) if isinstance(r, dict)]
    if not rows:
        return None
    sellable = 0
    counted = 0
    skipped = 0
    unnamed = 0
    for row in rows:
        name = _listing_name(row)
        if not name:
            # No name means nothing to classify. Counting it would score a
            # blank as private, therefore sellable, therefore a free pass.
            unnamed += 1
            continue
        try:
            verdict = lead_quality.classify(
                name,
                operator=row.get("operator") or "",
                operator_type=row.get("operator_type") or "")
            ok = bool(verdict.sellable)
        except (AttributeError, TypeError, ValueError) as e:
            # A row the classifier cannot judge must not be counted as
            # sellable OR unsellable: scoring it either way moves prong 4 on
            # evidence that does not exist. Logged, never silent - a prong
            # quietly computed over half the rows is the exact failure this
            # module exists to prevent.
            skipped += 1
            log.warning("niche_filter: unclassifiable listing %r (%s: %s)",
                        row.get("name"), type(e).__name__, e)
            continue
        counted += 1
        if ok:
            sellable += 1
    if skipped or unnamed:
        log.warning("niche_filter: prong 4 saw %d of %d listings "
                    "(%d unnamed, %d unclassifiable)",
                    counted, len(rows), unnamed, skipped)
    if not counted:
        log.warning("niche_filter: no listing carried a usable name key %s; "
                    "prong 4 stays UNMEASURED", list(_NAME_KEYS))
        return None

    # Named, not classified: a row that carried a name and then failed
    # classification still proves the scraper populated the field, which is
    # what this threshold is testing.
    named_share = (counted + skipped) / len(rows)
    if named_share < _MIN_NAMED_SHARE:
        # The contract is a measured number or nothing. A share computed over
        # a third of the rows is not evidence about the market, it is evidence
        # about the scraper.
        log.warning("niche_filter: only %.0f%% of listings carried a name "
                    "(need >= %.0f%%); prong 4 stays UNMEASURED",
                    named_share * 100, _MIN_NAMED_SHARE * 100)
        return None
    return sellable / counted


def report(verdict: NicheVerdict) -> str:
    marks = {PASS: "PASS", FAIL: "FAIL", UNKNOWN: "????"}
    lines = ["Niche: " + verdict.niche, ""]
    for p in verdict.prongs:
        lines.append("  [{}] {:<16} {}".format(marks[p.verdict], p.name, p.evidence))
    lines += ["", "  " + verdict.summary()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI. Self-contained on purpose: pipeline.py has no argparse entry point, and
# bolting one on there to reach this would risk the daily cron for no gain.
#
#   python -m modules.niche_filter --niche "Jaipur private dental" \
#       --labor-share 0.42 --top-player-share 0.05 --business-count 1800 \
#       --listings runs/raw_scrape.json
#
# Point --listings at the RAW scrape, never at runs/leads.json: that file is
# post-filter output and scores ~100% sellable by construction.
if __name__ == "__main__":
    import argparse
    import json as _json
    import sys as _sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    _ap = argparse.ArgumentParser(description="Score a niche against the four prongs.")
    _ap.add_argument("--niche", required=True)
    _ap.add_argument("--labor-share", type=float, default=None,
                     help="labour as a fraction of revenue, 0-1")
    _ap.add_argument("--staff-count", type=float, default=None)
    _ap.add_argument("--top-player-share", type=float, default=None, help="0-1")
    _ap.add_argument("--operators", type=float, default=None)
    _ap.add_argument("--largest-operator-sites", type=float, default=None)
    _ap.add_argument("--business-count", type=float, default=None)
    _ap.add_argument("--sellable-share", type=float, default=None, help="0-1")
    _ap.add_argument("--listings", default=None,
                     help="JSON file of RAW scraped listings; derives --sellable-share")
    _ap.add_argument("--json", action="store_true", help="machine-readable output")
    _args = _ap.parse_args()

    _sellable = _args.sellable_share
    if _args.listings:
        try:
            _raw = _json.loads(open(_args.listings, encoding="utf-8").read())
        except (OSError, ValueError) as _e:
            print("could not read {}: {}".format(_args.listings, _e), file=_sys.stderr)
            raise SystemExit(2)
        if isinstance(_raw, dict):
            _raw = _raw.get("leads") or _raw.get("listings") or _raw.get("results") or []
        _derived = sellable_share_from_listings(_raw)
        if _derived is None:
            print("could not derive a sellable share from {}; prong 4 stays "
                  "UNMEASURED".format(_args.listings), file=_sys.stderr)
        elif _sellable is None:
            _sellable = _derived
        elif abs(_derived - _sellable) > 0.01:
            print("NOTE: --sellable-share {:.0%} overrides the {:.0%} measured "
                  "from {}".format(_sellable, _derived, _args.listings), file=_sys.stderr)

    _v = evaluate(
        _args.niche,
        labor_share=_args.labor_share,
        staff_count=_args.staff_count,
        top_player_share=_args.top_player_share,
        operators=_args.operators,
        largest_operator_sites=_args.largest_operator_sites,
        business_count=_args.business_count,
        sellable_share=_sellable,
    )

    if _args.json:
        print(_json.dumps({
            "niche": _v.niche,
            "qualified": _v.qualified,
            "passed": _v.passed,
            "failures": _v.failures,
            "unmeasured": _v.unknowns,
            "summary": _v.summary(),
            "prongs": [{"name": p.name, "verdict": p.verdict, "evidence": p.evidence}
                       for p in _v.prongs],
        }, indent=2))
    else:
        print(report(_v))

    # Exit non-zero on anything short of a full pass, so a scripted sweep can
    # gate on it without parsing prose.
    raise SystemExit(0 if _v.qualified else 1)
