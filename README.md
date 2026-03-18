# Client Acquisition Pipeline

Automated daily pipeline: scrapes Indeed for urgent receptionist jobs → researches each company → generates personalized outreach → sends via Gmail + LinkedIn → creates Vapi demo agents on-demand when prospects reply.

## Setup (One-Time)

### 1. Install dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Gmail App Password
Google Account → Security → 2-Step Verification (enable) → App Passwords → Mail → Generate
Add to GitHub Secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

### 3. LinkedIn Cookies (burner account)
```bash
python scripts/save_linkedin_cookies.py
```
Log in, press ENTER, copy printed JSON → GitHub Secret: `LINKEDIN_COOKIES_JSON`
Re-run every 2–4 weeks when cookies expire.

### 4. Google Sheet
1. Create sheet with two tabs: `leads` and `errors`
2. Add header row to `leads`:
   `slug, company_name, website, domain, contact_name, email, linkedin_url, vapi_prompt, email_subject, email_body, linkedin_msg, email_sent, linkedin_sent, status, sent_at, replied_at, vapi_assistant_id`
3. Share with service account email (from your `GOOGLE_SERVICE_ACCOUNT_JSON`)
4. GitHub Secret: `GOOGLE_SHEETS_ID` (the ID from the sheet URL)

### 5. GitHub Secrets
Add all of these to your repo → Settings → Secrets and variables → Actions:
- `OPENROUTER_API_KEY` — free at openrouter.ai
- `VAPI_API_KEY` — your existing Vapi key
- `GOOGLE_SHEETS_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `LINKEDIN_COOKIES_JSON`
- `FIRECRAWL_API_KEY` — free at firecrawl.dev
- `SERPAPI_KEY` — free tier at serpapi.com

### 6. Local .env (for running scripts manually)
```bash
cp .env.example .env
# fill in your values
```

## Daily Usage

Pipeline runs automatically at 9am ET (2pm UTC). Open Google Sheet each morning — new leads are already researched and outreach sent.

## When a Prospect Replies

```bash
python scripts/create_agent.py <slug>
# example: python scripts/create_agent.py meridian-dental-llc
```

Open Vapi dashboard → find the assistant → Test tab → share screen on call → demo live.

## Customization

Edit `config.py` to change:
- `CITIES` — which cities to target
- `OPENROUTER_MODEL` — which AI model to use
- `LINKEDIN_DAILY_LIMIT` — max LinkedIn messages per day (default: 15)

## Cost

~$0/month at under 75 companies/day. All services use free tiers.
