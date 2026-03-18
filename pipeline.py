# pipeline.py
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from config import CITIES
from modules import scraper, researcher, generator, sheets_writer, email_sender, linkedin


def run():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Pipeline starting...")
    existing = sheets_writer.get_all_leads()
    total_saved = 0
    total_emailed = 0
    total_linkedin = 0

    for city in CITIES:
        print(f"  Scraping Indeed: {city}")
        try:
            jobs = scraper.run(city)
            print(f"    Found {len(jobs)} jobs")
        except Exception as e:
            print(f"    Scraper failed for {city}: {e}")
            continue

        for job in jobs:
            company = job["company_name"]
            try:
                if sheets_writer.domain_exists(job.get("domain"), existing):
                    print(f"    [SKIP] {company} (already in Sheets)")
                    continue

                print(f"    Processing: {company}")
                data = researcher.run(job)
                data = generator.run(data)

                saved = sheets_writer.save(data, existing)
                if saved:
                    total_saved += 1
                    existing.append({"domain": data.get("domain", "")})

                if data.get("email"):
                    emailed = email_sender.send(data)
                    if emailed:
                        sheets_writer.update_field(data["slug"], "email_sent", "TRUE")
                        sheets_writer.update_field(data["slug"], "sent_at",
                                                   datetime.now(timezone.utc).isoformat())
                        total_emailed += 1

                li_sent = linkedin.send(data)
                if li_sent:
                    sheets_writer.update_field(data["slug"], "linkedin_sent", "TRUE")
                    if not data.get("email"):
                        sheets_writer.update_field(data["slug"], "sent_at",
                                                   datetime.now(timezone.utc).isoformat())
                    total_linkedin += 1

            except Exception as e:
                print(f"    [ERROR] {company}: {e}")
                sheets_writer.log_error(company, str(e))
                continue

    print(f"\nDone. Saved: {total_saved} | Emailed: {total_emailed} | LinkedIn: {total_linkedin}")


if __name__ == "__main__":
    run()
