# Client Acquisition Pipeline — v2 Architecture

## What changed (Apr 2026 upgrade)

| Layer | Before | After |
|---|---|---|
| Input | Raw Indeed scrape | Scrape → enrich → score |
| Routing | Every lead gets full outreach | Score gates: ≥7 = full, 4–6 = email only, <4 = skip |
| Messages | 1 email body | 3 variants (pain/curiosity/ROI), auto-selected by niche |
| Post-send | Nothing | Reply classifier → response generator → follow-up scheduler |
| Analytics | None | Per-niche reply rate / booked call rate / conversion rate |
| Feedback | None | Optimizer every 50 leads → scoring weight recommendations |

---

## Lead Flow

```
Lead harvest (LEAD_SOURCE: indeed job-posts / apollo / osm fallback)
    ↓
researcher.run()     ← website, email, phone, services
    ↓
enricher.run()       ← niche, urgency, pain_signal, call_volume, revenue_dep
    ↓
scorer.run()         ← lead_score (0–10), lead_priority (high/medium/low)
    ↓
 score < 4? ──── SKIP (no outreach, no sheet row)
    ↓
generator.run()      ← 3 email variants + linkedin_msg + vapi_prompt
    ↓                   auto-selects best variant by niche/score
sheets_writer.save() ← full schema (see below)
    ↓
 score ≥ 7? ──── email + LinkedIn
 score 4–6? ──── email only
    ↓
 [daily, separate process]
reply_handler.run()  ← IMAP → classify → respond → update sheet
reply_handler.send_follow_ups() ← day 3 + day 7 nudges if no reply
    ↓
 [every 50 leads]
optimizer.run()      ← reads analytics → logs scoring + variant recommendations
```

---

## Variant Selection Logic

| Niche | Urgency | Selected Variant | Reasoning |
|---|---|---|---|
| dental / medical / legal / physio | high | pain | They know what's at stake. Hit the nerve. |
| dental / medical / legal / physio | medium/low | pain | Revenue dependency is still high |
| salon / trades / hotel | any | ROI | Numbers (revenue, calls) resonate more |
| school / general / unknown | any | curiosity | Lowest friction; works when pain isn't obvious |

Variant IDs stored in `message_variant_id` column for A/B tracking.

---

## Google Sheets Schema (leads tab)

### Original columns (positions preserved)
| Col | Field | Notes |
|---|---|---|
| A | slug | Unique ID |
| B | company_name | |
| C | website | |
| D | domain | Dedup key |
| E | contact_name | |
| F | email | |
| G | linkedin_url | |
| H | vapi_prompt | |
| I | email_subject | |
| J | email_body | Selected variant |
| K | linkedin_msg | |
| L | linkedin_post | |
| M | email_sent | TRUE/FALSE |
| N | linkedin_sent | TRUE/FALSE |
| O | status | pending/active/dead |
| P | sent_at | ISO timestamp |
| Q | replied_at | ISO timestamp |
| R | vapi_assistant_id | |

### New columns (appended — no column shift)
| Col | Field | Values | Purpose |
|---|---|---|---|
| S | niche | dental/medical/etc | Segment analytics by niche |
| T | lead_score | 0–10 | Routing + tracking quality |
| U | hiring_urgency | high/medium/low | Score input + copy context |
| V | pain_signal | scaling/turnover/etc | Copy personalisation |
| W | message_variant_id | pain/curiosity/roi | A/B performance tracking |
| X | channel_used | email/linkedin | Which channel first contact |
| Y | reply_status | interested/neutral/objection/not_relevant | Reply classification |
| Z | conversation_stage | initial/follow_up_1/follow_up_2/warm/booked/closed/dead | CTA ladder position |
| AA | objection_type | cost/timing/trust/existing_solution/other | Objection analysis |
| AB | follow_up_count | 0/1/2 | Max 2 follow-ups |
| AC | booked_call | yes/no | Primary conversion metric |
| AD | closed_client | yes/no | Final conversion metric |

### niche_analytics tab (auto-refreshed by analytics.run())
niche | total_sent | total_replies | total_booked_calls | total_clients | reply_rate | booked_call_rate | conversion_rate

### errors tab (existing)
company_name | error | timestamp

### optimizer_log tab (new)
timestamp | recommendations text

---

## n8n Equivalent Workflow

If migrating from Python script to n8n cloud automation:

```
[Schedule Trigger] 9am daily
    → [HTTP Request] SerpAPI Indeed (one request per city)
    → [Code] Parse jobs, filter duplicates by domain
    → [HTTP Request] Firecrawl scrape company website
    → [Code] enricher logic (niche/urgency/pain — pure Python regex, paste into Code node)
    → [Code] scorer logic (0–10 deterministic math)
    → [IF] lead_score < 4 → END (discard)
    → [HTTP Request] Groq API: generate 3 email variants + linkedin_msg
    → [Code] select variant by niche/score
    → [Google Sheets] Append row (full schema)
    → [IF] score ≥ 7 → [Gmail] Send email + [LinkedIn HTTP] send DM
    → [IF] score 4–6 → [Gmail] Send email only
    → [Google Sheets] Update email_sent / sent_at / channel_used

[Schedule Trigger] 10am daily (reply check)
    → [Gmail Trigger] fetch inbox, match sender to lead email
    → [HTTP Request] Groq API: classify reply
    → [Switch] category → (interested / neutral / objection / not_relevant)
    → [HTTP Request] Groq API: generate response (or use hardcoded objection rebuttal)
    → [Gmail] Send reply
    → [Google Sheets] Update reply_status, conversation_stage, replied_at

[Schedule Trigger] 11am daily (follow-ups)
    → [Google Sheets] Read all leads
    → [Code] Filter: sent_at ≤ 3 days ago, no reply, follow_up_count < 2
    → [Gmail] Send follow-up 1 or 2
    → [Google Sheets] Increment follow_up_count
```

---

## Failure Mode Fixes

| Problem | Root Cause | Fix |
|---|---|---|
| Low reply rate | Generic emails; no niche specificity | 3 variants + niche-selected; enricher adds context to prompt |
| Weak CTA | "Let me know if interested" → no action | CTA ladder: 5-min call ask → demo clip → booking link |
| Bad niche targeting | All leads treated equally | Scorer gates: schools and low-urgency skip entirely |
| Spam risk | High volume, no scoring | Score gate (min 4) reduces volume by ~30–40%; only high-quality leads get outreach |
| No trust factor | Pure cold outreach | linkedin_post skill builds authority in parallel (weekly posts) |
| Dead conversations | No reply handling | reply_handler: classify → respond → follow up × 2 |
| No feedback loop | Same weights forever | optimizer.run() every 50 leads surfaces what's working |

---

## Run Commands

```bash
# Main pipeline (scrape → enrich → score → generate → send)
python pipeline.py

# Reply handler + follow-ups (run daily, separate cron)
python pipeline.py replies

# Refresh niche analytics tab manually
python pipeline.py analytics
```

## .env additions needed

```
CAL_LINK=https://cal.com/yourlink   # for high-intent CTA in reply handler
```

---

## v3 — Architectural upgrade (May 2026)

> **2026-06-28 declutter, then v4 build.** First pass removed dead/stub modules;
> the same day several were rebuilt for real (see **v4** below). Net state:
> - `modules/deployment_telemetry.py` — **removed** (post-sale telemetry, never used).
> - `modules/instagram.py` — **re-added + wired** as a live send channel (informal
>   DM copy, hide-after-send so only repliers resurface). Gated by INSTAGRAM_ENABLED.
> - `modules/significance.py` — **re-added** (slim two-proportion z-test) to gate the
>   new self-improving loop.
> - `modules/maps_scraper.py` — **re-wired** as the Google Maps backfill (last paid
>   resort before the OSM floor), no longer orphaned.
> - `modules/icebreaker.py` + `scripts/harvest_local_leads.py` — stayed removed
>   (duplicated personalizer + the main funnel).

Addresses the operational gaps surfaced in the post-2-weeks-running review.
Everything in v3 is additive: legacy code paths and existing Sheet data are
untouched. New columns are appended at the end of HEADERS so column positions
don't shift.

### What changed

| Layer | v2 | v3 |
|---|---|---|
| Scoring | Single `lead_score` (pain only) | `pain_score` + `adoption_score` → weighted `lead_score` |
| Enrichment | niche + urgency + pain (3 fields) | + pms_signal + whatsapp_presence + instagram_presence + online_booking + multi_location + language_signal + review_velocity + budget_proxy + digital_maturity_score (11 fields total) |
| Channels | email → linkedin → whatsapp (single order) | Region-aware: **India** = whatsapp → instagram → email → linkedin. **Default** = unchanged v2 order. |
| Channel modules | linkedin, whatsapp | + **instagram** (stub, INSTAGRAM_ENABLED gate) |
| Variants | 5 (pain, curiosity, roi, question, + linkedin_msg, linkedin_post) | + **outcome** variant (operational result framing, default for India dental/medical/physio with adoption_score ≥ 5) |
| Follow-up caps | Single `follow_up_count`, cap 3 | Per-channel: email=5, whatsapp=3, instagram=2 (env-tunable). Legacy field still incremented. |
| Sender hygiene | Per-account daily cap 50, rotation | + warmup ramp (10/day week 1, 25/day week 2, 50/day week 3+), persisted in `sender_warmup` tab |
| Optimizer | Reports raw rates | Gated by **two-proportion z-test** (`modules/significance.py`); recommendations only emitted when p < 0.05 |
| Outcomes | `booked_call`, `closed_client` (binary) | + `monthly_value`, `cancelled_at`, `churn_reason`, `ltv`, `deployment_clinic_id` |
| Deployment | Untracked | New `deployments` tab + `modules/deployment_telemetry.py` (records calls, bookings, revenue per clinic; aggregates by niche for future scorer feedback) |

### v3 Sheets schema additions (leads tab)

Columns AN onward (appended — A through AM are unchanged):

| Col group | Fields |
|---|---|
| Digital maturity | `pms_signal`, `whatsapp_presence`, `instagram_presence`, `online_booking`, `multi_location`, `language_signal`, `review_velocity`, `budget_proxy` |
| Dual scoring | `digital_maturity_score`, `adoption_score`, `pain_score` |
| Instagram channel | `instagram_handle`, `instagram_msg`, `instagram_sent` |
| Per-channel follow-ups | `email_follow_up_count`, `whatsapp_follow_up_count`, `instagram_follow_up_count` |
| Close attribution | `monthly_value`, `cancelled_at`, `churn_reason`, `ltv`, `deployment_clinic_id` |

### New Sheet tabs

- **`sender_warmup`**: `address`, `first_send_date`, `total_sends`. Created lazily by `sender_warmup.record_send()` on first send from a new account.
- **`deployments`**: `clinic_id`, `lead_slug`, `clinic_name`, `city`, `niche`, `deployed_at`, `vapi_assistant_id`, `calls_handled`, `calls_missed_pre_ai`, `bookings_recovered`, `average_ticket_inr`, `revenue_recovered_inr`, `monthly_value_inr`, `active`, `churn_at`, `churn_reason`, `last_sync_at`. Created lazily by `deployment_telemetry._get_or_create_tab()`.

### v3 Lead Flow

```
Lead harvest (LEAD_SOURCE: indeed job-posts / apollo / osm fallback)
    ↓
researcher.run()           ← website, email, phone, services, IG handle, review count
    ↓
enricher.run()             ← niche/urgency/pain  +  v3 digital maturity (8 new fields)
    ↓
scorer.run()               ← pain_score + adoption_score → lead_score (60/40 weighted)
    ↓
 score < 4? ─── SKIP
    ↓
personalizer.run()         ← person_hook / company_hook
    ↓
generator.run()            ← 5 email variants (incl. outcome) + linkedin_msg + instagram_msg
    ↓                         (variant selector: India + outcome-niche + adoption≥5 → outcome)
sheets_writer.save()       ← all 60 columns
    ↓
 score ≥ 7:
   India lead  → whatsapp → instagram → email → linkedin (in priority order)
   non-India   → email → linkedin → whatsapp (unchanged v2 order)
 score 4–6    → email only (unchanged)
    ↓
 [3pm IST daily]
reply_handler.run()        ← inbox → classify → respond → update sheet
reply_handler.send_follow_ups()  ← up to 5 email touchpoints (day 3, 7, 12, 19, 29)
    ↓
 [every 50 leads]
optimizer.run()
    └─ significance.significance_test() gates recommendations on p<0.05

 [POST-SALE — currently stub]
deployment_telemetry.register_deployment(slug, ...)  ← run manually on first close
deployment_telemetry.record_call(clinic_id, ...)     ← VAPI webhook (TODO)
deployment_telemetry.aggregate_by_niche()            ← feeds back to scorer (TODO)
```

### v3 Variant Selection Logic

| Region | Niche | Adoption | Variant |
|---|---|---|---|
| India | dental / medical / physio | ≥5 | **outcome** (operational framing, no "AI") |
| any | dental / medical / legal / physio + urgency=high | any | pain |
| any | salon / trades / hotel | any | roi |
| any | dental / medical / legal / physio + priority=high | any | pain |
| any | general / school / unknown (no hooks) | any | question (Hormozi 3-line) |
| any | everything else | any | curiosity |

### Activation status (what's live vs stubbed)

| Component | Status | To activate |
|---|---|---|
| Schema columns | LIVE | New columns populate on next scrape |
| Enricher v3 signals | LIVE | Need researcher to populate `homepage_html`/`website_text`/`instagram_handle` for full coverage |
| Dual scoring | LIVE | Tunable via `SCORER_PAIN_WEIGHT` / `SCORER_ADOPTION_WEIGHT` env vars |
| Outcome variant | LIVE | Generator now emits 5 variants; selector picks outcome for India healthcare leads with adoption≥5 |
| India channel routing | LIVE | India leads go WhatsApp-first on next run |
| Instagram DM | STUB | Set `INSTAGRAM_ENABLED=1` + `INSTAGRAM_USERNAME`/`PASSWORD`, pre-warm account |
| Per-channel follow-ups | LIVE (email only) | WhatsApp/Instagram follow-ups need their own send loops |
| Sender warmup | LIVE | Pre-existing accounts default to full cap; warmup applies when first row appears in `sender_warmup` tab |
| Significance testing | LIVE | Optimizer needs to import and call `significance_test()` before recommending |
| Deployment telemetry | STUB | `register_deployment()` works manually. Auto-sync needs VAPI webhook integration |
| LTV / churn | LIVE | `sheets_writer.mark_deployed()` + `mark_churned()` ready to use |

### Env vars added in v3

```
# Scoring (optional — defaults shown)
SCORER_PAIN_WEIGHT=0.6
SCORER_ADOPTION_WEIGHT=0.4

# Follow-up caps (optional)
EMAIL_FOLLOWUP_MAX=5
WHATSAPP_FOLLOWUP_MAX=3
INSTAGRAM_FOLLOWUP_MAX=2

# Sender warmup (optional)
WARMUP_WEEK_1_CAP=10
WARMUP_WEEK_2_CAP=25
WARMUP_FULL_CAP=50

# Instagram channel (required to enable IG sends)
INSTAGRAM_USERNAME=...
INSTAGRAM_PASSWORD=...
INSTAGRAM_ENABLED=1
INSTAGRAM_DAILY_DM_LIMIT=20
INSTAGRAM_MIN_DELAY=45
```

### v3 design principles

1. **Additive only.** Every column is appended. Every function adds rather than replaces. Existing GitHub Actions runs keep passing.
2. **Stubs over half-implementations.** Instagram and deployment telemetry are stubs with clean interfaces. They wire into the pipeline routing but no-op until enabled. Better than partial code that pretends to work.
3. **Outcomes over signals.** The deployment telemetry tab is the long-term feedback loop. Once it has real data, the scorer should consume it directly and the regex enricher becomes a fallback.
4. **Region-aware, not region-locked.** Channel ordering, variant selection, and language signals all branch on region. Adding the next region (Australia, US) is a config addition, not a code rewrite.

> Note: the v3 tables above are the original design record. Where they describe
> `deployment_telemetry`, `significance`, or `instagram` as stubs, read the **v4**
> section below for the current built state.

---

## v4 — multi-source, Instagram, and the self-improving loop (Jun 2026)

### Lead sources (priority chain, per city)
`LEAD_SOURCE` picks the primary; the rest are automatic fallbacks:

```
primary = indeed (→ google_jobs fallback inside scraper.py)  |  apollo
    ↓  (primary < MAPS_BACKFILL_MIN leads AND SerpAPI quota left)
maps_scraper  — every local clinic/school via SerpAPI google_maps (last PAID resort)
    ↓  (still 0, or SerpAPI quota exhausted)
osm_scraper   — keyless OpenStreetMap floor (never sits silently at 0)
```

Every lead is stamped with `source_type` (indeed / google_jobs / apollo / maps /
osm), persisted to the Sheet and surfaced per-lead in the Command Center.

### Outreach channels (high-priority tier)
email + LinkedIn + WhatsApp + **Instagram**, fired in the **learned** order for the
lead's region. Instagram (`modules/instagram.py`) is send-only via instagrapi:
informal lowercase DM copy (`instagram_msg` from the generator), gated by
`INSTAGRAM_ENABLED`, and **hides each thread after sending** so only leads who
reply resurface in the inbox (`INSTAGRAM_HIDE_AFTER_SEND=1`). No IG auto-replies.

### Self-improving loop (`modules/learning.py`)
At the end of every run, learn from accumulated reply/booking outcomes and write
`runs/learned.json` (committed by CI) for the NEXT run to read. Three levers, each
gated by `modules/significance.py` (two-proportion z-test + min-sample) so it acts
on real edges, not noise:

| Lever | Learns | Applied by |
|---|---|---|
| `variant_by_niche` | best message angle per niche | `generator._select_variant` (epsilon-greedy: exploit winner, 20% explore) |
| `channel_order_by_region` | best channel order, India vs default | `pipeline.py` high-tier send loop |
| `scoring_weights` | pain vs adoption predictive power | `scorer._composite_weights` |

First run (no data/file): every reader falls back to its hard-coded default, so
behavior is unchanged until evidence accumulates. We learn from the PREVIOUS run
and apply to the NEXT — never mid-run.

### Command Center lead list
`pipeline.py` publishes `runs/leads.json` (label, source, niche, score, phone,
whatsapp, per-channel sent flags). The dashboard's **LEADS** tab fetches it via
`raw.githubusercontent` (same mechanism as `runs/latest.json`) — no new secrets.
