from datetime import datetime

from lead_scraper.google_sheets_writer import save_to_google_sheets


LEAD_COLUMNS = [
    ("Source", "source", 14),
    ("Posted At", "postedAt", 24),
    ("Score", "score", 10),
    ("Title", "title", 45),
    ("Description", "description", 80),
    ("Category", "category", 20),
    ("Job Type / Project Type", "projectType", 24),
    ("Country", "country", 20),
    ("Budget", "budget", 18),
    ("Company Name", "companyName", 28),
    ("Email", "email", 28),
    ("Phone Number", "phone", 20),
    ("URL", "url", 60),
    ("Posted Raw", "postedAtRaw", 18),
    ("Scraped At", "scrapedAt", 24),
    ("Score Reasons", "scoreReasons", 45),
    ("Lead Status", "leadStatus", 20),
    ("Dedupe Key", "dedupeKey", 66),
    ("Source Lead ID", "sourceLeadId", 22),
]

LOG_COLUMNS = [
    ("Run Time", "runTime", 24),
    ("Source", "source", 14),
    ("Category", "category", 22),
    ("Found", "found", 10),
    ("Saved", "saved", 10),
    ("Duplicates", "duplicates", 12),
    ("Skipped Old", "skippedOld", 12),
    ("Skipped Low Score", "skippedLowScore", 18),
    ("Errors", "errors", 40),
]


def serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return value if value is not None else ""


def read_existing_lead_keys():
    return []


def save_run_result(accepted_leads, rejected_leads, logs, all_leads=None):
    if all_leads is None:
        all_leads = [*accepted_leads, *rejected_leads]

    if save_to_google_sheets(accepted_leads, rejected_leads, all_leads, LEAD_COLUMNS):
        print("[GOOGLE-SHEETS] Saved leads to Google Sheets.")


def get_output_target():
    return "Google Sheets"
