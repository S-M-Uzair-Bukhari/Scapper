import time

import schedule

from lead_scraper.config import load_config
from lead_scraper.runner import run_once


def start_scheduler():
    config = load_config()
    enabled_sources = [source for source in config["sources"] if source.get("enabled")]

    if not enabled_sources:
        print("No enabled sources found in config/sources.json")
        return

    for index, source in enumerate(enabled_sources):
        minutes = max(10, int(source.get("intervalMinutes", 15)))
        source_name = source["name"]

        def job(name=source_name):
            print(f"Running {name}")
            run_once(name)

        schedule.every(minutes).minutes.do(job)
        print(f"Scheduled {source_name}: every {minutes} minutes")

        if index:
            time.sleep(1)

    print("Scheduler is running. Press Ctrl+C to stop.")

    while True:
        schedule.run_pending()
        time.sleep(1)

