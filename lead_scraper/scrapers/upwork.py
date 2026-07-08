import time
import re
import json
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from seleniumbase import Driver
from selenium.common.exceptions import WebDriverException
from pathlib import Path
from datetime import datetime, timedelta
from lead_scraper.config import from_root
from lead_scraper.date_parser import utc_now


BLOCKED_PATTERN = re.compile(r"security check|captcha|log in|sign in.*required|you need to login", re.I)


def build_search_url(source, category, page=1):
    base_url = source["baseSearchUrl"]
    separator = "?" if "?" not in base_url else "&"
    params = {
        "q": category["name"],
        "sort": "recency",
        "page": page
    }
    return f"{base_url}{separator}{urlencode(params)}"


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_category_name(category_name):
    return re.sub(r"[^a-z0-9]", "-", category_name, flags=re.I).lower()


def create_driver(source):
    profile_dir = source.get("profileDir", "data/chrome-profile/upwork")
    user_data_path = str(from_root(profile_dir))
    profile_name = "Default" 

    print(f"[UPWORK-DEBUG] Using Chrome profile path: {user_data_path}")
    print(f"[UPWORK-DEBUG] Using profile name: {profile_name}")
    Path(user_data_path).mkdir(parents=True, exist_ok=True)

    driver = Driver(
        uc=True,
        user_data_dir=user_data_path,
        headless=bool(source.get("headless")),
        chromium_arg=f"--profile-directory={profile_name} --disable-notifications --remote-debugging-port=9222"
    )
    
    if not source.get("headless"):
        driver.maximize_window()
                
    return driver


def save_debug(driver, category_name, meta=None):
    meta = meta or {}
    debug_dir = from_root("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)

    safe_category = safe_category_name(category_name)
    html_path = debug_dir / f"upwork-{safe_category}.html"
    json_path = debug_dir / f"upwork-{safe_category}.json"
    screenshot_path = debug_dir / f"upwork-{safe_category}.png"

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

    print("[UPWORK-DEBUG] Saved screenshot/html/meta in /debug")


def page_looks_blocked(driver):
    try:
        # Check if we're actually on the login page
        if "/login/" in driver.current_url or "/signin/" in driver.current_url:
            return True
            
        # Check for security blocks or captcha pages
        body_text = driver.find_element("body").text
        page_title = driver.title
        
        # Only block if we find actual security/captcha messages, not just the nav bar text
        security_issues = bool(re.search(r"security check|captcha|you are blocked|access denied", f"{page_title} {body_text}", re.I))
        
        # Check if job tiles exist - if they do, page is NOT blocked
        job_cards_exist = len(driver.find_elements("css selector", ".job-tile, a[href*='/jobs/~']")) > 0
        
        return security_issues and not job_cards_exist
    except WebDriverException:
        return True


def wait_for_manual_verification(driver, source):
    if not source.get("waitForManualVerification") or source.get("headless"):
        return False

    timeout_seconds = int(source.get("manualVerificationTimeoutMs", 120000)) / 1000
    started_at = time.time()

    print("[UPWORK] Waiting for manual verification/login in the visible Chrome window...")

    while time.time() - started_at < timeout_seconds:
        if not page_looks_blocked(driver):
            print("[UPWORK] Manual verification appears complete.")
            return True
        time.sleep(3)

    return False


def scroll_results(driver):
    for _ in range(5):
        driver.execute_script("window.scrollBy(0, 1200)")
        time.sleep(0.8)


def extract_job_details(driver, basic_job):
    page_html = driver.page_source
    soup = BeautifulSoup(page_html, "html.parser")
    
    job_details = basic_job.copy()
    
    # Extract company/client name with updated selectors for Upwork's current UI
    company_name = ""
    company_selectors = [
        "[data-test='company-name']",
        "[data-test='client-company-name']",
        ".client-name a",
        ".company-name",
        "h3:-soup-contains('About the client') + div a",
        "div[data-test='AboutClient'] a",
        ".air3-card-section a[href*='/organizations/']",
        ".up-card-section a[href*='/organizations/']",
        "[data-qa='client-name']",
        ".air3-typography[data-test='company-name']",
        ".client-info a"
    ]
    
    for selector in company_selectors:
        try:
            el = soup.select_one(selector)
            if el:
                company_name = clean_text(el.get_text())
                if company_name:
                    break
        except Exception:
            continue
    
    project_type = "individual"
    if company_name:
        project_type = "company"
    
    # Extract location with updated selectors for Upwork's current UI
    location = ""
    location_selectors = [
        "[data-test='location']",
        "[data-qa='client-location']",
        ".client-location",
        "div[data-test='AboutClient'] div:-soup-contains('Location') + div",
        ".air3-card-section div:-soup-contains('Location') + div"
    ]
    
    for selector in location_selectors:
        try:
            el = soup.select_one(selector)
            if el:
                location = clean_text(el.get_text())
                break
        except Exception:
            continue
    
    # Extract contact information - only find real emails/phones in the job description
    job_description = soup.select_one("[data-test='job-description-text']")
    desc_text = job_description.get_text() if job_description else ""
    
    email = ""
    phone = ""
    # Only search for emails within the actual job description, not the whole page
    email_matches = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", desc_text)
    if email_matches:
        email = email_matches[0]
    
    # Only search for valid phone numbers in the job description (min 10 digits, avoid random numbers)
    phone_matches = re.findall(r"\+?[\d\s-]{10,}", desc_text)
    valid_phones = [p for p in phone_matches if sum(c.isdigit() for c in p) >= 10]
    if valid_phones:
        phone = valid_phones[0]
    
    job_details.update({
        "companyName": company_name,
        "projectType": project_type,
        "location": location,
        "email": email,
        "phone": phone
    })
    
    print(f"[UPWORK-DEBUG] Extracted details: Company='{company_name}', ProjectType='{project_type}', Location='{location}'")
    return job_details


def collect_job_cards_local(html_source):
    soup = BeautifulSoup(html_source, "html.parser")
    jobs = []

    selectors = [
        "a[href*='/jobs/~']",
        "a[href*='/freelance-jobs/apply/']",
        ".job-tile a",
        ".air3-card-list a",
        "article a"
    ]
    
    title_links = soup.select(", ".join(selectors))

    for link in title_links:
        card = (
            link.find_parent(attrs={"class": "job-tile"}) or
            link.find_parent("article") or
            link.find_parent(".air3-card-list") or
            link.find_parent("section") or
            link.parent
        )

        title = link.get_text() or ""
        full_text = card.get_text() if card else ""
        href = link.get("href", "")
        
        if not ("/jobs/~" in href or "/freelance-jobs/apply/" in href or "job-tile" in str(card.get('class', ''))):
            continue

        if href.startswith("/"):
            href = f"https://www.upwork.com{href}"

        desc_text = ""
        if card:
            desc_el = (card.select_one("[data-test='job-description-text']") or 
                      card.select_one("p") or 
                      card.select_one(".job-description") or
                      card.select_one("div[class*='description']"))
            desc_text = desc_el.get_text() if desc_el else ""

        posted_match = re.search(
            r"(Posted\s+)?(\d+\s+(minute|minutes|hour|hours|day|days|week|weeks)\s+ago|Today|Yesterday)",
            full_text, re.I
        )
        budget_match = re.search(
            r"\$\d+(?:[,\.]\d+)?(?:\s*-\s*\$\d+(?:[,\.]\d+)?)?|Hourly:\s*\$\d+-\$\d+|Fixed-price:\s*\$\d+(?:,\d+)?",
            full_text, re.I
        )

        jobs.append({
            "title": clean_text(title),
            "url": href,
            "description": clean_text(desc_text),
            "postedAtRaw": posted_match.group(0) if posted_match else "",
            "budget": budget_match.group(0) if budget_match else "",
            "source": "upwork"
        })

    return jobs


def calculate_base_score(job):
    """Calculate base score from critical fields - these are the HIGHEST weighted"""
    base_score = 0
    # Highest priority: contact info and company details (max 100 points from these alone)
    if job.get("companyName"):
        base_score += 30  # Company name exists
    if job.get("location"):
        base_score += 25  # Location exists
    if job.get("email"):
        base_score += 30  # Email exists (highest value contact field)
    if job.get("phone"):
        base_score += 15  # Phone exists
    
    # Add small freshness bonus (doesn't override critical fields priority)
    if job.get("postedAtRaw"):
        posted_raw = job["postedAtRaw"].lower()
        if "hour" in posted_raw or "minute" in posted_raw or "today" in posted_raw:
            base_score += 5  # Small bonus for very fresh leads
        elif "day" in posted_raw and ("1" in posted_raw or "2" in posted_raw):
            base_score += 2  # Tiny bonus for leads posted in last 48h
    
    # Critical fields are always highest - even with bonuses, cap base at 100
    return min(base_score, 100)


def parse_posted_at(posted_at_raw, scraped_at):
    if not posted_at_raw:
        return None, ""
    
    posted_at_raw = posted_at_raw.lower()
    now = scraped_at
    
    if "today" in posted_at_raw:
        return now.isoformat(), posted_at_raw
    elif "yesterday" in posted_at_raw:
        return (now - timedelta(days=1)).isoformat(), posted_at_raw
    
    minute_match = re.search(r"(\d+)\s*minute", posted_at_raw)
    hour_match = re.search(r"(\d+)\s*hour", posted_at_raw)
    day_match = re.search(r"(\d+)\s*day", posted_at_raw)
    week_match = re.search(r"(\d+)\s*week", posted_at_raw)
    
    if minute_match:
        minutes = int(minute_match.group(1))
        return (now - timedelta(minutes=minutes)).isoformat(), posted_at_raw
    elif hour_match:
        hours = int(hour_match.group(1))
        return (now - timedelta(hours=hours)).isoformat(), posted_at_raw
    elif day_match:
        days = int(day_match.group(1))
        return (now - timedelta(days=days)).isoformat(), posted_at_raw
    elif week_match:
        weeks = int(week_match.group(1))
        return (now - timedelta(weeks=weeks)).isoformat(), posted_at_raw
    
    return None, posted_at_raw


def scrape_upwork(source, category, config):
    scraped_at = utc_now()
    max_pages = min(source.get("maxPages", 3), 3)
    all_detailed_jobs = []
    driver = create_driver(source)

    try:
        print("[UPWORK] Pre-warming session configuration on homepage...")
        driver.get("https://upwork.com")
        time.sleep(5)

        print(f"[UPWORK] Current URL after homepage: {driver.current_url}")
        
        for page in range(1, max_pages + 1):
            page_url = build_search_url(source, category, page)
            print(f"[UPWORK] Target Opening page {page}/{max_pages}: {page_url}")
            driver.get(page_url)
            
            page_load_wait_ms = source.get("pageLoadWaitMs")
            if page_load_wait_ms:
                time.sleep(int(page_load_wait_ms) / 1000)
            
            print(f"[UPWORK] Current URL after navigating to search page {page}: {driver.current_url}")
            print(f"[UPWORK] Page title: {driver.title}")

            blocked = page_looks_blocked(driver)
            print(f"[UPWORK-DEBUG] Page blocked check result: {blocked}")
            if blocked:
                print(f"[UPWORK-DEBUG] Page title when blocked: {driver.title}")
                print(f"[UPWORK-DEBUG] Body text snippet: {driver.find_element('body').text[:500]}...")
                verified = wait_for_manual_verification(driver, source)
                if not verified and page_looks_blocked(driver):
                    save_debug(driver, category["name"], {
                        "source": "upwork",
                        "category": category["name"],
                        "url": page_url,
                        "reason": "blocked-or-login-page",
                    })
                    print("[UPWORK] CAPTCHA/login/security page is still present. Skipping remaining pages.")
                    break

            try:
                print(f"[UPWORK] Waiting for job cards to load on page {page}...")
                driver.wait_for_element_present(".job-tile, .air3-card-list, a[href*='/jobs/~']", timeout=30)
                print(f"[UPWORK] Job cards found successfully on page {page}!")
            except Exception as e:
                print(f"[UPWORK-ERROR] Failed to find job cards on page {page}: {str(e)}")
                save_debug(driver, category["name"], {
                    "source": "upwork",
                    "category": category["name"],
                    "url": page_url,
                    "reason": "no-job-cards-found",
                    "error": str(e)
                })
                continue

            scroll_results(driver)
            time.sleep(3)
            
            page_html = driver.page_source
            print(f"[UPWORK-DEBUG] Page source length on page {page}: {len(page_html)}")
            raw_jobs = collect_job_cards_local(page_html)
            print(f"[UPWORK] Found {len(raw_jobs)} job URLs on search page {page}")
            
            page_detailed_jobs = []
            for i, job in enumerate(raw_jobs[:source.get("maxResultsPerRun", 100)]):
                print(f"[UPWORK] Scraping details for job {i+1}/{len(raw_jobs)} on page {page}: {job['title']}")
                try:
                    driver.get(job["url"])
                    time.sleep(3)
                    
                    job_details = extract_job_details(driver, job)
                    job_details["score"] = calculate_base_score(job_details)
                    page_detailed_jobs.append(job_details)
                    print(f"[UPWORK] Successfully scraped details for: {job['title']} (Score: {job_details['score']})")
                    
                    time.sleep(2)
                except Exception as e:
                    print(f"[UPWORK-ERROR] Failed to scrape details for {job['url']}: {str(e)}")
                    job["score"] = 0
                    page_detailed_jobs.append(job)
            
            all_detailed_jobs.extend(page_detailed_jobs)
            
            if page < max_pages:
                time.sleep(int(source.get("delayBetweenPagesMs", 2500)) / 1000)

        raw_jobs = all_detailed_jobs
        print(f"[UPWORK] Total raw jobs: {len(raw_jobs)} category: {category['name']}")

        unique_jobs = {}
        for job in raw_jobs:
            if len(unique_jobs) >= source.get("maxResultsPerRun", 100):
                break

            posted_at, posted_at_raw = parse_posted_at(job.get("postedAtRaw"), scraped_at)
            source_lead_id_match = re.search(r"~[a-z0-9]+", job.get("url", ""), re.I)
            apply_match = re.search(r"/apply/([^/?]+)", job.get("url", ""), re.I)
            source_lead_id = (
                source_lead_id_match.group(0)
                if source_lead_id_match
                else apply_match.group(1)
                if apply_match
                else None
            )

            if not source_lead_id:
                continue

            if source_lead_id in unique_jobs:
                continue

            # Clean location and extract country for Excel compatibility
            job_location = job.get("location", "")
            # Remove any timestamps that might be scraped incorrectly
            job_location = re.sub(r"\d{1,2}:\d{2}\s*(AM|PM)?", "", job_location, flags=re.I).strip()
            # Extract country from cleaned location
            country = job_location.split(",")[-1].strip() if "," in job_location else job_location
            
            unique_jobs[source_lead_id] = {
                "source": "upwork",
                "sourceLeadId": source_lead_id,
                "title": job.get("title", ""),
                "url": job.get("url", ""),
                "description": job.get("description", ""),
                "category": category["name"],  # Required field for Excel writer
                "country": country,  # Required field for Excel writer
                "budget": job.get("budget", ""),
                "postedAt": posted_at,
                "postedAtRaw": posted_at_raw,
                "companyName": job.get("companyName", ""),
                "location": job_location,
                "email": job.get("email", ""),
                "phone": job.get("phone", ""),
                "projectType": job.get("projectType", "individual"),
                "score": job.get("score", 0),
                "scrapedAt": scraped_at.isoformat(),
            }

        print(f"[UPWORK] Processed {len(unique_jobs)} unique leads for category: {category['name']}")
        return list(unique_jobs.values())

    finally:
        if source.get("keepBrowserOpen"):
            print("[UPWORK] Keeping Chrome open because keepBrowserOpen=true.")
        else:
            driver.quit()