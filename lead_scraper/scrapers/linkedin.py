import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException
from seleniumbase import Driver

from lead_scraper.config import from_root
from lead_scraper.date_parser import parse_posted_at, utc_now


BLOCKED_PATTERN = re.compile(r"captcha|security check|sign in|join linkedin|authwall", re.I)
JOB_ID_PATTERN = re.compile(r"/jobs/view/(\d+)|currentJobId=(\d+)|jobPostingId=(\d+)", re.I)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_category_name(category_name):
    return re.sub(r"[^a-z0-9]", "-", category_name, flags=re.I).lower()


def build_search_url(source, category, page=1):
    base_url = source.get("baseSearchUrl", "https://www.linkedin.com/jobs/search/")
    params = {
        "keywords": category["name"],
        "sortBy": source.get("sortBy", "DD"),
        "start": (page - 1) * int(source.get("resultsPerPage", 25)),
    }

    if source.get("location"):
        params["location"] = source["location"]

    if source.get("timeFilter"):
        params["f_TPR"] = source["timeFilter"]
    else:
        params["f_TPR"] = "r86400"

    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def create_driver(source):
    profile_dir = source.get("profileDir", "data/chrome-profile/linkedin")
    user_data_path = str(from_root(profile_dir))
    profile_name = source.get("profileName", "Default")

    print(f"[LINKEDIN-DEBUG] Using Chrome profile path: {user_data_path}")
    Path(user_data_path).mkdir(parents=True, exist_ok=True)

    driver = Driver(
        uc=True,
        user_data_dir=user_data_path,
        headless=bool(source.get("headless")),
        chromium_arg=f"--profile-directory={profile_name} --disable-notifications",
    )

    if not source.get("headless"):
        driver.maximize_window()

    return driver


def save_debug(driver, category_name, meta=None):
    meta = meta or {}
    debug_dir = from_root("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    safe_category = safe_category_name(category_name)
    html_path = debug_dir / f"linkedin-{safe_category}.html"
    json_path = debug_dir / f"linkedin-{safe_category}.json"
    screenshot_path = debug_dir / f"linkedin-{safe_category}.png"

    title = ""
    current_url = ""
    html = ""

    try:
        title = driver.title
        current_url = driver.current_url
        html = driver.page_source
        driver.save_screenshot(str(screenshot_path))
    except WebDriverException:
        pass

    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps({**meta, "title": title, "currentUrl": current_url}, indent=2), encoding="utf-8")
    print("[LINKEDIN-DEBUG] Saved screenshot/html/meta in /debug")


def page_looks_blocked(driver):
    try:
        body_text = driver.find_element("body").text
    except WebDriverException:
        body_text = ""

    return bool(BLOCKED_PATTERN.search(f"{driver.title} {body_text}"))


def wait_for_manual_verification(driver, source):
    if not source.get("waitForManualVerification") or source.get("headless"):
        return False

    timeout_seconds = int(source.get("manualVerificationTimeoutMs", 120000)) / 1000
    started_at = time.time()

    print("[LINKEDIN] Waiting for manual login/verification in the visible Chrome window...")
    while time.time() - started_at < timeout_seconds:
        if not page_looks_blocked(driver):
            print("[LINKEDIN] Manual verification appears complete.")
            return True
        time.sleep(3)

    return False


def scroll_results(driver):
    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 1200)")
        time.sleep(0.8)


def first_text(container, selectors):
    for selector in selectors:
        element = container.select_one(selector)
        if element:
            value = clean_text(element.get_text(" "))
            if value:
                return value
    return ""


def first_attr(container, selectors, attr):
    for selector in selectors:
        element = container.select_one(selector)
        if element and element.get(attr):
            return clean_text(element.get(attr))
    return ""


def normalize_url(url):
    if not url:
        return ""
    if url.startswith("/"):
        url = f"https://www.linkedin.com{url}"
    return url.split("?")[0]


def extract_source_lead_id(url):
    match = JOB_ID_PATTERN.search(url or "")
    if not match:
        return None
    return next((group for group in match.groups() if group), None)


def detect_country(text, config):
    haystack = (text or "").lower()
    for country in config.get("countries", []):
        aliases = [country["name"], *(country.get("aliases") or [])]
        if any(alias.lower() in haystack for alias in aliases):
            return country["name"]
    return ""


def collect_job_cards(html_source, category, config, scraped_at):
    soup = BeautifulSoup(html_source, "html.parser")
    jobs = []
    seen = set()

    selectors = [
        "li.jobs-search-results__list-item",
        "div.job-card-container",
        "div.base-card",
        "div.job-search-card",
        "a[href*='/jobs/view/']",
    ]

    for item in soup.select(", ".join(selectors)):
        card = (
            item.find_parent("li", class_=re.compile("jobs-search-results__list-item"))
            or item.find_parent("div", class_=re.compile("job-card|base-card"))
            or item
        )

        url = normalize_url(first_attr(card, [
            "a[href*='/jobs/view/']",
            "a.job-card-container__link",
            "a.base-card__full-link",
        ], "href"))

        source_lead_id = extract_source_lead_id(url)
        if not source_lead_id or source_lead_id in seen:
            continue

        title = first_text(card, [
            ".base-search-card__title",
            ".job-card-list__title",
            ".job-card-container__link",
            "a[href*='/jobs/view/']",
        ])

        company = first_text(card, [
            ".base-search-card__subtitle",
            ".job-card-container__primary-description",
            ".job-card-container__company-name",
        ])

        location = first_text(card, [
            ".job-search-card__location",
            ".job-card-container__metadata-item",
            ".job-card-container__metadata-wrapper li",
        ])

        posted_raw = first_attr(card, ["time"], "datetime") or first_text(card, [
            "time",
            ".job-search-card__listdate",
            ".job-card-container__listed-time",
        ])
        posted_at, posted_at_raw = parse_posted_at(posted_raw, scraped_at)

        seen.add(source_lead_id)
        jobs.append({
            "source": "linkedin",
            "sourceLeadId": source_lead_id,
            "title": title,
            "url": url,
            "description": clean_text(" ".join(part for part in [company, location] if part)),
            "budget": "",
            "postedAt": posted_at,
            "postedAtRaw": posted_at_raw or "",
            "companyName": company,
            "location": location,
            "country": detect_country(location, config),
            "projectType": "job",
            "category": category["name"],
            "scrapedAt": scraped_at.isoformat(),
        })

    return jobs


def extract_job_details(driver, job, config):
    soup = BeautifulSoup(driver.page_source, "html.parser")

    title = first_text(soup, [
        ".top-card-layout__title",
        ".jobs-unified-top-card__job-title",
        "h1",
    ])
    company = first_text(soup, [
        ".topcard__org-name-link",
        ".top-card-layout__second-subline a",
        ".jobs-unified-top-card__company-name",
    ])
    location = first_text(soup, [
        ".topcard__flavor--bullet",
        ".jobs-unified-top-card__bullet",
        ".jobs-unified-top-card__primary-description-container",
    ])
    description = first_text(soup, [
        ".description__text",
        ".show-more-less-html__markup",
        ".jobs-description-content__text",
        "#job-details",
    ])

    if title:
        job["title"] = title
    if company:
        job["companyName"] = company
    if location:
        job["location"] = location
        job["country"] = detect_country(location, config)
    if description:
        job["description"] = description

    return job


def scrape_linkedin(source, category, config):
    scraped_at = utc_now()
    max_pages = int(source.get("maxPages", 2))
    max_results = int(source.get("maxResultsPerRun", 100))
    page_load_wait_ms = int(source.get("pageLoadWaitMs", 6000))
    delay_between_pages_ms = int(source.get("delayBetweenPagesMs", 3000))
    scrape_details = source.get("scrapeDetails", True)

    driver = create_driver(source)
    all_jobs = []

    try:
        for page in range(1, max_pages + 1):
            page_url = build_search_url(source, category, page)
            print(f"[LINKEDIN] Opening page {page}/{max_pages}: {page_url}")
            driver.get(page_url)
            time.sleep(page_load_wait_ms / 1000)

            if page_looks_blocked(driver):
                verified = wait_for_manual_verification(driver, source)
                if not verified and page_looks_blocked(driver):
                    save_debug(driver, category["name"], {
                        "source": "linkedin",
                        "category": category["name"],
                        "url": page_url,
                        "reason": "blocked-login-or-security-page",
                    })
                    print("[LINKEDIN] Login/security page is still present. Skipping remaining pages.")
                    break

            scroll_results(driver)
            jobs = collect_job_cards(driver.page_source, category, config, scraped_at)
            print(f"[LINKEDIN] Found {len(jobs)} jobs on page {page}")

            for job in jobs:
                if len(all_jobs) >= max_results:
                    break

                if scrape_details and job.get("url"):
                    try:
                        print(f"[LINKEDIN] Scraping details for: {job['title'] or job['url']}")
                        driver.get(job["url"])
                        time.sleep(2)
                        job = extract_job_details(driver, job, config)
                    except Exception as error:
                        print(f"[LINKEDIN-ERROR] Failed to scrape details for {job['url']}: {error}")

                all_jobs.append(job)

            if len(all_jobs) >= max_results:
                break

            if page < max_pages:
                time.sleep(delay_between_pages_ms / 1000)

        print(f"[LINKEDIN] Processed {len(all_jobs)} leads for category: {category['name']}")
        return all_jobs[:max_results]

    finally:
        if source.get("keepBrowserOpen"):
            print("[LINKEDIN] Keeping Chrome open because keepBrowserOpen=true.")
        else:
            driver.quit()
