"""
scripts/harvest_local_leads.py — Populate the freshly-cleaned sheet with local
clinics & schools, each with a phone number and a lead-specific icebreaker.

SOURCE = "both, merged":
  * Google Maps  -> every clinic/school in the city (phone-rich). source_type=maps
  * Indeed       -> the same niches that are ACTIVELY HIRING a receptionist.
                    These are the hot leads. A Maps row that matches a hiring
                    company (by domain or normalized name) is flagged
                    hiring_now=yes; hiring companies with no Maps match are added
                    as their own source_type=indeed rows.

HARVEST ONLY — never sends email / WhatsApp / Instagram / LinkedIn.
Per lead:  enricher (niche) -> scorer -> icebreaker -> sheets_writer.save

Run via Doppler:
    doppler run --project client-acquisition-pipeline --config dev -- \
        python scripts/harvest_local_leads.py [--limit N] [--dry-run] [--no-hiring]
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CITIES  # noqa: E402
from modules import (  # noqa: E402
    maps_scraper, scraper, researcher, enricher, scorer, icebreaker, sheets_writer,
)

EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"

# We are niching DOWN to clinics + schools. Everything else is dropped.
TARGET_NICHES = {"dental", "medical", "physio", "optometry", "school"}

# Generic tokens stripped when normalizing a business name for matching.
_STOP = {
    "the", "and", "clinic", "clinics", "dental", "dentist", "hospital", "care",
    "centre", "center", "school", "schools", "academy", "institute", "multispeciality",
    "multispecialty", "speciality", "specialty", "pvt", "ltd", "private", "limited",
    "drs", "dr", "polyclinic", "healthcare", "health", "medical", "physiotherapy",
    "physio", "eye", "vision", "public", "international", "english", "medium",
}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_name(name: str) -> str:
    toks = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower()).split()
    core = [t for t in toks if t not in _STOP]
    return "".join(core) or "".join(toks)


def _dedup_key(lead: dict) -> str:
    if lead.get("domain"):
        return f"domain:{lead['domain'].lower()}"
    if lead.get("phone"):
        return f"phone:{_digits(lead['phone'])}"
    return f"name:{_norm_name(lead.get('company_name',''))}"


def _enrich_niche(lead: dict) -> dict:
    lead = enricher.run(lead)
    if lead.get("maps_niche") and lead.get("niche") in ("general", None, ""):
        lead["niche"] = lead["maps_niche"]
    return lead


def _build_hiring_index() -> tuple[dict, dict, list]:
    """Scrape Indeed receptionist posts, keep clinic/school niches.
    Returns (by_domain, by_name, leftover_leads)."""
    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    leftovers: list[dict] = []
    for city in CITIES:
        try:
            jobs = scraper.run(city)
        except Exception as e:
            print(f"  [HIRING] Indeed failed for {city}: {e}")
            continue
        print(f"  [HIRING] {city}: {len(jobs)} receptionist post(s)")
        for job in jobs:
            try:
                e = _enrich_niche(job)
            except Exception:
                continue
            if e.get("niche") not in TARGET_NICHES:
                continue
            e["hiring_now"] = "yes"
            nm = _norm_name(e.get("company_name", ""))
            dom = (e.get("domain") or "").lower()
            if dom:
                by_domain[dom] = e
            if nm:
                by_name[nm] = e
            leftovers.append(e)
    print(f"  [HIRING] clinic/school leads hiring now: {len(leftovers)}")
    return by_domain, by_name, leftovers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max leads to save (0 = no cap)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-hiring", action="store_true", help="skip the Indeed hiring layer")
    args = ap.parse_args()

    existing = sheets_writer.get_all_leads()
    seen: set[str] = set()
    for row in existing:
        if row.get("domain"):
            seen.add(f"domain:{row['domain'].lower()}")
        if row.get("phone"):
            seen.add(f"phone:{_digits(row['phone'])}")

    # ── Phase A: who is hiring right now (Indeed) ────────────────────────────
    hire_dom, hire_name, hire_leftovers = ({}, {}, [])
    if not args.no_hiring:
        print("\n### Phase A — hiring index (Indeed)")
        hire_dom, hire_name, hire_leftovers = _build_hiring_index()
    matched_hiring: set[str] = set()

    saved: list[dict] = []

    def _finish(lead: dict) -> bool:
        """enrich-score-icebreak-save one lead. Returns True if saved."""
        try:
            lead = scorer.run(lead)
            lead = icebreaker.run(lead)
        except Exception as e:
            print(f"  [ERROR] finish {lead.get('company_name')}: {e}")
            return False
        hot = " 🔥HIRING" if lead.get("hiring_now") == "yes" else ""
        print(f"  + [{lead.get('source_type')}] {lead['company_name']}  "
              f"[{lead.get('niche')}]  {lead.get('phone') or 'NO PHONE'}{hot}")
        print(f"      “{lead.get('icebreaker','')}”")
        if not args.dry_run:
            try:
                if sheets_writer.save(lead, existing):
                    existing.append({"domain": lead.get("domain", ""),
                                     "phone": lead.get("phone", "")})
            except Exception as e:
                print(f"  [ERROR] save {lead.get('company_name')}: {e}")
                return False
        saved.append(lead)
        return True

    # ── Phase B: Maps harvest (all clinics/schools) ──────────────────────────
    print("\n### Phase B — Maps harvest (clinics + schools)")
    stop = False
    for city in CITIES:
        if stop:
            break
        print(f"\n=== {city} ===")
        try:
            leads = maps_scraper.run(city)
        except Exception as e:
            print(f"  [ERROR] maps scrape failed for {city}: {e}")
            continue
        for lead in leads:
            key = _dedup_key(lead)
            if key in seen:
                continue
            seen.add(key)
            lead = _enrich_niche(lead)
            if lead.get("niche") not in TARGET_NICHES:
                continue
            lead["source_type"] = "maps"
            # Flag hot if this business is also hiring right now.
            dom = (lead.get("domain") or "").lower()
            nm = _norm_name(lead.get("company_name", ""))
            if dom and dom in hire_dom:
                lead["hiring_now"] = "yes"; matched_hiring.add(f"domain:{dom}")
            elif nm and nm in hire_name:
                lead["hiring_now"] = "yes"; matched_hiring.add(f"name:{nm}")
            else:
                lead["hiring_now"] = "no"
            _finish(lead)
            if args.limit and len(saved) >= args.limit:
                stop = True
                break

    # ── Phase C: hiring leads with no Maps match (enrich for phone) ──────────
    if not args.no_hiring and not (args.limit and len(saved) >= args.limit):
        print("\n### Phase C — hiring leads not found on Maps")
        for lead in hire_leftovers:
            dom = (lead.get("domain") or "").lower()
            nm = _norm_name(lead.get("company_name", ""))
            if f"domain:{dom}" in matched_hiring or f"name:{nm}" in matched_hiring:
                continue
            key = _dedup_key(lead)
            if key in seen:
                continue
            seen.add(key)
            try:
                lead = researcher.run(lead)   # scrape site for phone/email
                lead = _enrich_niche(lead)
            except Exception as e:
                print(f"  [ERROR] research {lead.get('company_name')}: {e}")
            lead["source_type"] = "indeed"
            lead["hiring_now"] = "yes"
            _finish(lead)
            if args.limit and len(saved) >= args.limit:
                break

    hot = sum(1 for d in saved if d.get("hiring_now") == "yes")
    withphone = sum(1 for d in saved if d.get("phone"))
    print(f"\nNew leads: {len(saved)} | with phone: {withphone} | "
          f"hiring-now (hot): {hot} | dry-run: {args.dry_run}")

    if saved and not args.dry_run:
        EXPORT_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = EXPORT_DIR / f"icebreakers-{stamp}.md"
        lines = [
            f"# Local clinics & schools — icebreakers ({stamp})",
            "",
            f"Source: Google Maps + Indeed (hiring). Cities: {', '.join(CITIES)}. "
            f"Total: {len(saved)} ({hot} hiring now).",
            "",
            "| Business | Niche | City | Phone | Hot | Icebreaker |",
            "|---|---|---|---|---|---|",
        ]
        for d in sorted(saved, key=lambda x: x.get("hiring_now") != "yes"):
            ice = (d.get("icebreaker", "") or "").replace("|", "/").replace("\n", " ")
            hotmark = "🔥" if d.get("hiring_now") == "yes" else ""
            lines.append(
                f"| {d.get('company_name','')} | {d.get('niche','')} | "
                f"{d.get('location','')} | {d.get('phone','')} | {hotmark} | {ice} |"
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[EXPORT] {path}")


if __name__ == "__main__":
    main()
