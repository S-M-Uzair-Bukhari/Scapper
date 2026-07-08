import argparse
from datetime import datetime, timezone

from lead_scraper.excel_writer import get_output_file, save_run_result
from lead_scraper.runner import run_once
from lead_scraper.scheduler import start_scheduler


def main():
    parser = argparse.ArgumentParser(
        prog="lead-scraper",
        description="Scrape fresh leads, score them, dedupe them, and save priority leads to Excel.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once_parser = subparsers.add_parser("run-once", help="Run all enabled scrapers one time.")
    run_once_parser.add_argument("--source", help="Optional single source name, for example upwork.")

    subparsers.add_parser("schedule", help="Run enabled scrapers on their configured schedules.")
    subparsers.add_parser("smoke-excel", help="Create/update the workbook and append a harmless smoke log row.")

    args = parser.parse_args()

    if args.command == "run-once":
        run_once(args.source)
    elif args.command == "schedule":
        start_scheduler()
    elif args.command == "smoke-excel":
        save_run_result(
            accepted_leads=[],
            rejected_leads=[],
            logs=[
                {
                    "runTime": datetime.now(timezone.utc).isoformat(),
                    "source": "smoke",
                    "category": "system",
                    "found": 0,
                    "saved": 0,
                    "duplicates": 0,
                    "skippedOld": 0,
                    "skippedLowScore": 0,
                    "errors": "",
                }
            ],
        )
        print(f"Excel smoke file ready: {get_output_file()}")
