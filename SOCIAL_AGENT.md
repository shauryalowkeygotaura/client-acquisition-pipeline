# Social Agent — free "posts, replies, qualifies leads across N platforms"

A free reimplementation of the FuturMinds "Hermes" agent
(`youtube.com/watch?v=cpfC_87tPPo`). It does the three jobs the video promises,
using **Groq** (free) as the brain and your existing pipeline modules — no paid
APIs, no SaaS.

| Job | How |
|-----|-----|
| **Post** | `content_engine.py` → Groq writes one on-brand post → published to every live connector |
| **Reply** | `social_brain.py` → Groq classifies each inbound and drafts a reply in the right register |
| **Qualify** | same classifier tags business leads (niche + intent), logs them, optionally pushes to the leads sheet |

It runs end to end with **zero config** via the `console` connector. Real
platforms are opt-in, each one free.

## Run it

```powershell
# free, zero-config demo (console connector). Drop test messages into
# runs/console_inbox.jsonl first (one JSON per line: {"id","text","author"}).
doppler run -- python social_agent.py once

# modes
python social_agent.py post            # generate + publish one post
python social_agent.py engage          # poll, qualify, DRAFT replies
python social_agent.py engage --send   # actually send replies
python social_agent.py loop            # engage every cycle, post every Nth
python social_agent.py post --series build-log --topic "what broke today"
```

Secrets come from Doppler (`project: client-acquisition-pipeline`). Only
`GROQ_API_KEY` is required; it's already there.

## Safety rails

- Replies are **drafted to `runs/social_drafts.jsonl`, not sent**, unless you
  pass `--send` or set `SOCIAL_AUTO_REPLY=1`. Review before going live.
- Qualified leads are always logged free to `runs/social_leads.jsonl`. They are
  pushed to the Google Sheet only if `SOCIAL_SAVE_LEADS=1`.
- All `runs/social_*` + `console_*` files are gitignored (they hold lead PII).

## Platforms

Set `SOCIAL_PLATFORMS` (comma separated). Default: `console`.

| Platform | Env vars (all free to obtain) | Post | Read/Reply |
|----------|-------------------------------|:----:|:----------:|
| `console` | — | ✅ | ✅ (from `runs/console_inbox.jsonl`) |
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | ✅ | ✅ |
| `discord` | `DISCORD_WEBHOOK_URL` (post) and/or `DISCORD_BOT_TOKEN`+`DISCORD_CHANNEL_ID` (read) | ✅ | ✅ |

Adding a platform = one new file in `modules/connectors/` subclassing
`Connector` (post / fetch_inbound / reply), registered in `registry._BUILDERS`.
Mastodon, Bluesky, Reddit, and reuse of the existing Instagram/LinkedIn/WhatsApp
modules are the obvious next connectors — same three verbs each.

## Tuning (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `SOCIAL_PLATFORMS` | `console` | which connectors load |
| `SOCIAL_AUTO_REPLY` | `0` | `1` = send replies instead of drafting |
| `SOCIAL_SAVE_LEADS` | `0` | `1` = also write qualified leads to the sheet |
| `SOCIAL_MAX_REPLIES` | `20` | per-run reply cap |
| `SOCIAL_POST_EVERY_CYCLES` | `8` | in `loop`, post once per N cycles |
| `SOCIAL_LOOP_SLEEP` | `300` | seconds between `loop` cycles |
| `BRAND_NAME` / `BRAND_HANDLE` / `BRAND_WHO` / `BRAND_AUDIENCE` | Revengine | post voice |

## Schedule it free

Windows Task Scheduler (same pattern as the research loop), or GitHub Actions
cron, calling `doppler run -- python social_agent.py once` (or `engage`). The
agent writes `run_metrics` each run, so it shows up on the Command Center
dashboard like the other pipelines.

## Tests

```powershell
python -m pytest tests/test_social_agent.py -q   # 13 offline tests, no network
```
