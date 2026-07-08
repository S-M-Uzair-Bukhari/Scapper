import queue
import threading
import os
from datetime import datetime, timezone

from lead_scraper.config import load_config
from lead_scraper.date_parser import is_within_hours
from lead_scraper.dedupe import create_dedupe_key, is_duplicate
from lead_scraper.excel_writer import get_output_target, read_existing_lead_keys, save_run_result
from lead_scraper.scorer import score_lead
from lead_scraper.scrapers.facebook import scrape_facebook
from lead_scraper.scrapers.google import scrape_google_projects
from lead_scraper.scrapers.instagram import scrape_instagram
from lead_scraper.scrapers.linkedin import scrape_linkedin
from lead_scraper.scrapers.upwork import scrape_upwork


SCRAPERS = {
    "upwork": scrape_upwork,
    "linkedin": scrape_linkedin,
    "linkedin-jobs": scrape_linkedin,
    "linkedin-posts": scrape_linkedin,
    "google-projects": scrape_google_projects,
    "facebook": scrape_facebook,
    "instagram": scrape_instagram,
}


def get_max_workers(config):
    return max(1, int(os.getenv("SCRAPER_MAX_WORKERS", config.get("maxWorkers", 2))))


def get_profile_key(source):
    return source.get("profileDir") or source["name"]


def make_log(source, category):
    return {
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


def scrape_job(source, category, config, profile_locks):
    log = make_log(source, category)
    scraper = SCRAPERS.get(source["name"])
    if not scraper:
        log["errors"] = f"No scraper registered for source: {source['name']}"
        return [], log

    profile_key = get_profile_key(source)
    profile_lock = profile_locks[profile_key]

    try:
        print(f"[WORKER:{source['name']}] Waiting for profile lock: {profile_key}")
        with profile_lock:
            print(f"[WORKER:{source['name']}] Scraping category: {category['name']}")
            leads = scraper(source, category, config)

        print(f"[WORKER:{source['name']}] Leads returned for {category['name']}: {len(leads)}")
        if leads:
            print(f"[WORKER:{source['name']}] First lead sample:", leads[0])

        log["found"] = len(leads)
        return leads, log
    except Exception as error:
        log["errors"] = str(error)
        return [], log


def run_scrape_workers(enabled_sources, categories, config):
    jobs = queue.Queue()
    results = queue.Queue()
    profile_locks = {
        get_profile_key(source): threading.Lock()
        for source in enabled_sources
    }

    for source in enabled_sources:
        if source["name"] not in SCRAPERS:
            for category in categories:
                results.put(([], make_log(source, category)))
            continue

        for category in categories:
            jobs.put((source, category))

    max_workers = min(get_max_workers(config), max(1, jobs.qsize()))
    print(f"[RUNNER] Source queue workers: {max_workers}")

    def worker_loop(worker_id):
        while True:
            try:
                source, category = jobs.get_nowait()
            except queue.Empty:
                return

            try:
                print(f"[WORKER:{worker_id}] Picked {source['name']} / {category['name']}")
                results.put(scrape_job(source, category, config, profile_locks))
            finally:
                jobs.task_done()

    workers = [
        threading.Thread(target=worker_loop, args=(index + 1,), daemon=True)
        for index in range(max_workers)
    ]

    for worker in workers:
        worker.start()

    jobs.join()

    for worker in workers:
        worker.join()

    scrape_results = []
    while not results.empty():
        scrape_results.append(results.get())

    return scrape_results


def process_leads(leads, log, existing, accepted_leads, rejected_leads, all_leads, config):
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
            scored["leadStatus"] = "priority"
            accepted_leads.append(scored)
            all_leads.append(scored)
            existing.append(scored)
            log["saved"] += 1
        else:
            scored["leadStatus"] = "rejectedLowScore"
            rejected_leads.append(scored)
            all_leads.append(scored)
            existing.append(scored)
            log["skippedLowScore"] += 1


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

    for leads, log in run_scrape_workers(enabled_sources, config["categories"], config):
        process_leads(leads, log, existing, accepted_leads, rejected_leads, all_leads, config)
        logs.append(log)

    save_run_result(accepted_leads, rejected_leads, logs, all_leads=all_leads)

    print(f"Accepted priority leads: {len(accepted_leads)}")
    print(f"Rejected low-score leads: {len(rejected_leads)}")
    print(f"Run log rows written: {len(logs)}")
    print(f"Output target: {get_output_target()}")
