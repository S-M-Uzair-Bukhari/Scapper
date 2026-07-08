import argparse
from datetime import datetime, timezone

from lead_scraper.excel_writer import get_output_file, save_run_result
from lead_scraper.runner import run_once
from lead_scraper.scheduler import start_scheduler


def main():
    parser = argparse.ArgumentParser(description="Scrape, score, dedupe, and save leads to Excel.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once_parser = subparsers.add_parser("run-once", help="Run enabled scrapers once.")
    run_once_parser.add_argument("--source", default=None, help="Optional source name, for example upwork.")

    subparsers.add_parser("schedule", help="Run enabled scrapers on configured schedules.")
    subparsers.add_parser("smoke-excel", help="Create/update the workbook and append a harmless smoke log row.")

    args = parser.parse_args()

    if args.command == "run-once":
        run_once(args.source)
    elif args.command == "schedule":
        start_scheduler()
    elif args.command == "smoke-excel":
        save_run_result(
            [],
            [],
            [
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


if __name__ == "__main__":
    main()
