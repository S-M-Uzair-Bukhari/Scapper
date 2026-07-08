import json
import os
from pathlib import Path

from lead_scraper.config import from_root


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LEAD_SHEET_NAMES = ("Priority Leads", "All Scraped Leads", "Rejected Low Score")
LOG_SHEET_NAME = "Run Logs"
DEFAULT_API_TIMEOUT_SECONDS = 120
DEFAULT_API_RETRIES = 3


def load_env_file():
    env_path = from_root(".env")
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def load_settings():
    load_env_file()

    settings = {}
    settings_path = from_root("config", "google_sheets.json")

    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as file:
            settings = json.load(file)

    spreadsheet_id = (
        os.getenv("GOOGLE_SHEETS_ID")
        or os.getenv("GOOGLE_SPREADSHEET_ID")
        or os.getenv("SPREADSHEET_ID")
        or settings.get("spreadsheetId")
    )
    service_account_file = (
        os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or settings.get("serviceAccountFile")
    )

    enabled = settings.get("enabled", True)
    if str(os.getenv("GOOGLE_SHEETS_ENABLED", enabled)).lower() in ("0", "false", "no"):
        enabled = False

    return {
        "enabled": enabled,
        "spreadsheetId": spreadsheet_id,
        "serviceAccountFile": service_account_file,
    }


def is_configured():
    settings = load_settings()
    return bool(settings["enabled"] and settings["spreadsheetId"] and settings["serviceAccountFile"])


def resolve_service_account_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = from_root(value)
    return path


def get_service(settings):
    try:
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError(
            "Google Sheets output requires google-api-python-client, google-auth, and google-auth-httplib2. "
            "Run: python -m pip install -r requirements.txt"
        ) from error

    credentials_path = resolve_service_account_path(settings["serviceAccountFile"])
    if not credentials_path.exists():
        raise RuntimeError(f"Google service account file not found: {credentials_path}")

    credentials = Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
    timeout_seconds = int(os.getenv("GOOGLE_API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT_SECONDS))
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout_seconds))
    return build("sheets", "v4", http=http, cache_discovery=False)


def api_retries():
    return int(os.getenv("GOOGLE_API_RETRIES", DEFAULT_API_RETRIES))


def execute_request(request):
    return request.execute(num_retries=api_retries())


def get_http_error_details(error):
    try:
        payload = json.loads(error.content.decode("utf-8"))
    except Exception:
        return str(error), None, None

    error_body = payload.get("error", {})
    message = error_body.get("message") or str(error)
    reason = None
    activation_url = None

    for detail in error_body.get("details", []):
        if detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
            reason = detail.get("reason")
        if detail.get("@type") == "type.googleapis.com/google.rpc.Help":
            for link in detail.get("links", []):
                if link.get("url"):
                    activation_url = link["url"]
                    break

    return message, reason, activation_url


def print_google_sheets_error(error):
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        print(f"[GOOGLE-SHEETS] Failed: {error}")
        return

    if not isinstance(error, HttpError):
        if isinstance(error, OSError) and getattr(error, "winerror", None) == 10060:
            print("[GOOGLE-SHEETS] Network timeout while connecting to Google Sheets.")
            print("[GOOGLE-SHEETS] Check your internet/VPN/firewall, then run again. You can also increase GOOGLE_API_TIMEOUT_SECONDS in .env.")
            return
        print(f"[GOOGLE-SHEETS] Failed: {error}")
        return

    message, reason, activation_url = get_http_error_details(error)

    if reason == "SERVICE_DISABLED":
        print("[GOOGLE-SHEETS] Google Sheets API is disabled for this service-account project.")
        if activation_url:
            print(f"[GOOGLE-SHEETS] Enable it here, wait a few minutes, then run again: {activation_url}")
        else:
            print("[GOOGLE-SHEETS] Enable Google Sheets API in the Google Cloud project for this service account.")
        return

    if error.resp.status in (403, 404):
        print("[GOOGLE-SHEETS] Could not access the spreadsheet.")
        print("[GOOGLE-SHEETS] Share the Google Sheet with the service account client_email and verify GOOGLE_SHEETS_ID.")
        print(f"[GOOGLE-SHEETS] Google said: {message}")
        return

    print(f"[GOOGLE-SHEETS] Failed: {message}")


def quote_sheet_name(name):
    return "'" + name.replace("'", "''") + "'"


def serialize_rows(columns, rows):
    from lead_scraper.excel_writer import serialize_value

    return [
        [serialize_value(row.get(key)) for _header, key, _width in columns]
        for row in rows
    ]


def ensure_sheets(service, spreadsheet_id, lead_columns, log_columns=None):
    spreadsheet = execute_request(service.spreadsheets().get(spreadsheetId=spreadsheet_id))
    existing_titles = {sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])}
    requests = []

    sheet_names = list(LEAD_SHEET_NAMES)
    if log_columns is not None:
        sheet_names.append(LOG_SHEET_NAME)

    for title in sheet_names:
        if title not in existing_titles:
            requests.append({"addSheet": {"properties": {"title": title}}})

    if requests:
        execute_request(service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ))

    headers_by_sheet = {
        title: [header for header, _key, _width in lead_columns]
        for title in LEAD_SHEET_NAMES
    }
    if log_columns is not None:
        headers_by_sheet[LOG_SHEET_NAME] = [header for header, _key, _width in log_columns]

    for title, headers in headers_by_sheet.items():
        range_name = f"{quote_sheet_name(title)}!1:1"
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name,
        )
        result = execute_request(result)
        current_headers = result.get("values", [[]])[0] if result.get("values") else []

        if current_headers != headers:
            execute_request(service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [headers]},
            ))


def append_sheet_rows(service, spreadsheet_id, sheet_name, columns, rows):
    if not rows:
        return

    execute_request(service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet_name(sheet_name)}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": serialize_rows(columns, rows)},
    ))


def save_to_google_sheets(accepted_leads, rejected_leads, all_leads, columns, logs=None, log_columns=None):
    settings = load_settings()
    if not settings["enabled"]:
        return False

    if not settings["spreadsheetId"] or not settings["serviceAccountFile"]:
        return False

    try:
        service = get_service(settings)
        spreadsheet_id = settings["spreadsheetId"]

        ensure_sheets(service, spreadsheet_id, columns, log_columns)
        append_sheet_rows(service, spreadsheet_id, "Priority Leads", columns, accepted_leads)
        append_sheet_rows(service, spreadsheet_id, "Rejected Low Score", columns, rejected_leads)
        append_sheet_rows(service, spreadsheet_id, "All Scraped Leads", columns, all_leads)
        append_sheet_rows(service, spreadsheet_id, LOG_SHEET_NAME, log_columns, logs or [])
    except Exception as error:
        print_google_sheets_error(error)
        return False

    return True
