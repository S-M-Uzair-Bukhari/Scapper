from datetime import datetime, timezone

from lead_scraper.config import load_config
from lead_scraper.date_parser import is_within_hours
from lead_scraper.dedupe import create_dedupe_key, is_duplicate
from lead_scraper.excel_writer import get_output_target, read_existing_lead_keys, save_run_result
from lead_scraper.scorer import score_lead
from lead_scraper.scrapers.facebook import scrape_facebook
from lead_scraper.scrapers.instagram import scrape_instagram
from lead_scraper.scrapers.linkedin import scrape_linkedin
from lead_scraper.scrapers.upwork import scrape_upwork


SCRAPERS = {
    "upwork": scrape_upwork,
    "linkedin": scrape_linkedin,
    "facebook": scrape_facebook,
    "instagram": scrape_instagram,
}


def run_once(source_name=None):
    config = load_config()
    enabled_sources = [
        source for source in config["sources"]
        if source.get("enabled") and (source_name is None or source["name"] == source_name)
    ]

    existing = read_existing_lead_keys()
    accepted_leads = []
    all_leads = []
    rejected_leads = []
    logs = []

    print("Enabled sources:", ", ".join(source["name"] for source in enabled_sources) or "none")
    print("Enabled categories:", ", ".join(category["name"] for category in config["categories"]) or "none")

    for source in enabled_sources:
        scraper = SCRAPERS.get(source["name"])
        if not scraper:
            continue

        for category in config["categories"]:
            log = {
                "runTime": datetime.now(timezone.utc).isoformat(),
                "source": source["name"],
                "category": category["name"],
                "found": 0,
                "saved": 0,
                "duplicates": 0,
                "skippedOld": 0,
                "skippedLowScore": 0,
                "errors": "",
            }

            try:
                leads = scraper(source, category, config)
                print("[RUNNER] Leads returned:", len(leads))
                if leads:
                    print("[RUNNER] First lead sample:", leads[0])

                log["found"] = len(leads)

                for lead in leads:
                    lead["dedupeKey"] = create_dedupe_key(lead)
                    scored = {**lead, **score_lead(lead, config)}

                    if not is_within_hours(lead.get("postedAt"), config["scoring"]["freshWindowHours"]):
                        all_leads.append({**scored, "leadStatus": "skippedOld"})
                        log["skippedOld"] += 1
                        continue

                    if is_duplicate(lead, existing) or is_duplicate(lead, accepted_leads) or is_duplicate(lead, rejected_leads):
                        all_leads.append({**scored, "leadStatus": "duplicate"})
                        log["duplicates"] += 1
                        continue

                    if scored["score"] >= config["scoring"]["minimumPriorityScore"]:
                        # 70+ points: Save to priority leads (highest quality)
                        scored["leadStatus"] = "priority"
                        accepted_leads.append(scored)
                        all_leads.append(scored)
                        existing.append(scored)
                        log["saved"] += 1
                    elif scored["score"] < 50:
                        # Below 50 points: Save to rejected (low quality)
                        scored["leadStatus"] = "rejectedLowScore"
                        rejected_leads.append(scored)
                        all_leads.append(scored)
                        existing.append(scored)
                        log["skippedLowScore"] += 1
                    else:
                        # 50-69 points: Still save to all leads but not priority
                        scored["leadStatus"] = "rejectedLowScore"
                        rejected_leads.append(scored)
                        all_leads.append(scored)
                        existing.append(scored)
                        log["skippedLowScore"] += 1
            except Exception as error:
                log["errors"] = str(error)

            logs.append(log)

    save_run_result(accepted_leads, rejected_leads, logs, all_leads=all_leads)

    print(f"Accepted priority leads: {len(accepted_leads)}")
    print(f"Rejected low-score leads: {len(rejected_leads)}")
    print(f"Run log rows written: {len(logs)}")
    print(f"Output target: {get_output_target()}")