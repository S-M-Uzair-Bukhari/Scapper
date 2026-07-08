# Lead Scraper

Python backend scraper that collects fresh leads, scores them, removes duplicates, and saves leads to Google Sheets.

## What Is Included

- Python runner, scheduler, scoring, dedupe, date parsing, and Google Sheets writing.
- Selenium-based Upwork scraper as the first implemented source.
- Placeholder scrapers for LinkedIn, Facebook, and Instagram.
- Configurable categories, countries, sources, and scoring rules in `config/`.
- 24-48 hour freshness filter.
- Score threshold of 70+ for priority leads.
- Google Sheets output with priority, all scraped, and rejected lead tabs.
- Duplicate detection using URL, source lead ID, and dedupe hash.
- Scheduler that runs every 10-15 minutes based on `config/sources.json`.
- Visible Chrome/manual verification support using a persistent profile.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run Once

```bash
python -m lead_scraper run-once
```

Run only one source:

```bash
python -m lead_scraper run-once --source upwork
```

## Run Scheduler

```bash
python -m lead_scraper schedule
```

## Google Sheets Output

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Add your spreadsheet ID to `.env`:

```bash
GOOGLE_SHEETS_ID=YOUR_GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_FILE=config/google-service-account.json
```

Put your service-account JSON key at `config/google-service-account.json`, then share the Google Sheet with the service account email from that JSON file.

Google Sheets will use these tabs: `Priority Leads`, `All Scraped Leads`, and `Rejected Low Score`.

## Upwork Browser Profile

The Upwork scraper uses Selenium with a persistent Chrome profile:

```text
data/chrome-profile/upwork
```

The config currently opens Chrome visibly:

```json
"headless": false,
"waitForManualVerification": true,
"manualVerificationTimeoutMs": 120000
```

If Upwork shows login or verification, complete it manually in the opened Chrome window. The profile is reused on later runs.

## Notes

This project is designed for public pages/search results and respectful crawling. Keep request rates low, avoid bypassing platform protections, and review each platform's terms before enabling a source.
