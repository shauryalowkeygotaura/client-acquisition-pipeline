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
Indeed scrape (city × 30 jobs)
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
