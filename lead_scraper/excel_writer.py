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
    """Read existing leads from Google Sheets to check for duplicates across runs"""
    from lead_scraper.google_sheets_writer import load_settings, is_configured, get_service, execute_request, quote_sheet_name
    
    if not is_configured():
        print("[EXCEL-WRITER] Google Sheets not configured, can't check for existing leads")
        return []
    
    settings = load_settings()
    if not settings["spreadsheetId"]:
        return []
    
    try:
        service = get_service(settings)
        # Read from "All Scraped Leads" sheet which contains all previously saved leads
        range_name = f"{quote_sheet_name('All Scraped Leads')}!A:Z"
        result = service.spreadsheets().values().get(
            spreadsheetId=settings["spreadsheetId"],
            range=range_name,
        )
        values = execute_request(result).get("values", [])
        
        if not values:
            return []
        
        # Get column indices to map values correctly
        headers = values[0]
        url_index = headers.index("URL") if "URL" in headers else -1
        source_lead_id_index = headers.index("Source Lead ID") if "Source Lead ID" in headers else -1
        
        existing_leads = []
        # Skip header row, process all existing leads
        for row in values[1:]:
            lead = {}
            if url_index >= 0 and len(row) > url_index:
                lead["url"] = row[url_index]
            if source_lead_id_index >= 0 and len(row) > source_lead_id_index:
                lead["sourceLeadId"] = row[source_lead_id_index]
            existing_leads.append(lead)
        
        print(f"[EXCEL-WRITER] Loaded {len(existing_leads)} existing leads from sheet for deduplication")
        return existing_leads
        
    except Exception as e:
        print(f"[EXCEL-WRITER] Error reading existing leads: {str(e)}")
        return []


def save_run_result(accepted_leads, rejected_leads, logs, all_leads=None):
    if all_leads is None:
        all_leads = [*accepted_leads, *rejected_leads]

    if save_to_google_sheets(accepted_leads, rejected_leads, all_leads, LEAD_COLUMNS, logs, LOG_COLUMNS):
        print("[GOOGLE-SHEETS] Saved leads to Google Sheets.")


def get_output_target():
    return "Google Sheets"
