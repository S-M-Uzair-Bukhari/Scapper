import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException
from seleniumbase import Driver

from lead_scraper.config import from_root
from lead_scraper.date_parser import utc_now


BLOCKED_PATTERN = re.compile(r"captcha|unusual traffic|sorry|verify you are human", re.I)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_category_name(category_name):
    return re.sub(r"[^a-z0-9]", "-", category_name, flags=re.I).lower()


def create_driver(source):
    profile_dir = source.get("profileDir", "data/chrome-profile/google")
    user_data_path = str(from_root(profile_dir))
    profile_name = source.get("profileName", "Default")

    print(f"[GOOGLE-DEBUG] Using Chrome profile path: {user_data_path}")
    Path(user_data_path).mkdir(parents=True, exist_ok=True)

    driver = Driver(
        uc=True,
        user_data_dir=user_data_path,
        headless=bool(source.get("headless", True)),
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
    html_path = debug_dir / f"google-{safe_category}.html"
    json_path = debug_dir / f"google-{safe_category}.json"
    screenshot_path = debug_dir / f"google-{safe_category}.png"

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
    print("[GOOGLE-DEBUG] Saved screenshot/html/meta in /debug")


def page_looks_blocked(driver):
    try:
        body_text = driver.find_element("body").text
    except WebDriverException:
        body_text = ""

    return bool(BLOCKED_PATTERN.search(f"{driver.title} {body_text}"))


def build_queries(source, category):
    templates = source.get("queryTemplates") or [
        '"looking for" "{category}" "project"',
        '"need" "{category}" "developer"',
        '"hiring" "{category}" "project"',
        '"{category}" "send proposal"',
    ]

    return [template.format(category=category["name"]) for template in templates]


def build_search_url(query, page=1):
    params = {
        "q": query,
        "num": 10,
        "start": (page - 1) * 10,
        "tbs": "qdr:d",
    }
    return "https://www.google.com/search?" + urlencode(params)


def normalize_result_url(url):
    if not url:
        return ""

    if url.startswith("/url?"):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        url = query.get("q", [""])[0]

    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com"):
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""

    return url


def source_lead_id(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def detect_country(text, config):
    haystack = (text or "").lower()
    for country in config.get("countries", []):
        if not isinstance(country, dict):
            continue
        aliases = [country.get("name", ""), *(country.get("aliases") or [])]
        if any(alias and alias.lower() in haystack for alias in aliases):
            return country["name"]
    return ""


def collect_results(html_source, source, category, config, scraped_at):
    soup = BeautifulSoup(html_source, "html.parser")
    leads = []

    result_cards = soup.select("div.g, div.MjjYud")
    for card in result_cards:
        link = card.select_one("a[href]")
        title_el = card.select_one("h3")
        if not link or not title_el:
            continue

        url = normalize_result_url(link.get("href", ""))
        if not url:
            continue

        title = clean_text(title_el.get_text(" "))
        description = clean_text(
            " ".join(
                item.get_text(" ")
                for item in card.select("div.VwiC3b, div.yXK7lf, span.aCOpRe")
            )
        )

        if not description:
            description = clean_text(card.get_text(" "))

        text = f"{title} {description}"
        country = detect_country(text, config)

        leads.append({
            "source": source["name"],
            "sourceLeadId": source_lead_id(url),
            "title": title,
            "url": url,
            "description": description[:1000],
            "category": category["name"],
            "country": country,
            "budget": "",
            "postedAt": scraped_at.isoformat(),
            "postedAtRaw": "google last 24 hours",
            "scrapedAt": scraped_at.isoformat(),
            "companyName": "",
            "location": country,
            "email": "",
            "phone": "",
            "projectType": "google-project",
        })

    return leads


def scrape_google_projects(source, category, config):
    scraped_at = utc_now()
    max_results = int(source.get("maxResultsPerRun", 25))
    max_pages = int(source.get("maxPages", 1))
    page_load_wait_ms = int(source.get("pageLoadWaitMs", 4000))
    delay_between_pages_ms = int(source.get("delayBetweenPagesMs", 2000))
    seen = set()
    all_leads = []

    driver = create_driver(source)

    try:
        for query in build_queries(source, category):
            if len(all_leads) >= max_results:
                break

            for page in range(1, max_pages + 1):
                if len(all_leads) >= max_results:
                    break

                url = build_search_url(query, page)
                print(f"[GOOGLE] Opening page {page}/{max_pages}: {url}")
                driver.get(url)
                time.sleep(page_load_wait_ms / 1000)

                if page_looks_blocked(driver):
                    save_debug(driver, category["name"], {
                        "source": source["name"],
                        "category": category["name"],
                        "url": url,
                        "reason": "blocked-or-captcha",
                    })
                    print("[GOOGLE] Google blocked the search page or showed CAPTCHA. Stopping Google source.")
                    return all_leads[:max_results]

                results = collect_results(driver.page_source, source, category, config, scraped_at)
                print(f"[GOOGLE] Found {len(results)} results for query: {query}")

                for lead in results:
                    if lead["sourceLeadId"] in seen:
                        continue
                    seen.add(lead["sourceLeadId"])
                    all_leads.append(lead)
                    if len(all_leads) >= max_results:
                        break

                if page < max_pages:
                    time.sleep(delay_between_pages_ms / 1000)

        print(f"[GOOGLE] Finished. Total leads found: {len(all_leads)} for {category['name']}")
        return all_leads[:max_results]

    finally:
        if source.get("keepBrowserOpen", False):
            print("[GOOGLE] Keeping Chrome open because keepBrowserOpen=true.")
        else:
            driver.quit()
