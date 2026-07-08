import time
import re
import json
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from seleniumbase import Driver
from selenium.common.exceptions import WebDriverException
from pathlib import Path
from lead_scraper.config import from_root
from lead_scraper.date_parser import utc_now


BLOCKED_PATTERN = re.compile(r"security check|captcha|log in|sign in.*required|you need to login", re.I)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_category_name(category_name):
    return re.sub(r"[^a-z0-9]", "-", category_name, flags=re.I).lower()


def create_driver(source):
    """Use the same proven driver setup as Upwork - works with existing LinkedIn profile"""
    profile_dir = source.get("profileDir", "data/chrome-profile/linkedin")
    user_data_path = str(from_root(profile_dir))
    profile_name = "Default" 

    print(f"[LINKEDIN-DEBUG] Using Chrome profile path: {user_data_path}")
    Path(user_data_path).mkdir(parents=True, exist_ok=True)

    driver = Driver(
        uc=True,
        user_data_dir=user_data_path,
        headless=bool(source.get("headless", False)),
        chromium_arg=f"--profile-directory={profile_name} --disable-notifications --remote-debugging-port=9223"
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
        # Check if we're actually on the login page
        if "/login/" in driver.current_url or "/signin/" in driver.current_url:
            print(f"[LINKEDIN-DEBUG] Detected login page: {driver.current_url}")
            return True
            
        # Check for security blocks or captcha pages
        body_text = driver.find_element("body").text
        page_title = driver.title
        
        # Check if we have any job or post cards - updated post selectors to match current LinkedIn
        job_cards = driver.find_elements("css selector", 'div[data-job-id]')
        post_cards = driver.find_elements("css selector", 'div[data-urn], div.reusable-search__result-container, li.search-result')
        job_cards_exist = len(job_cards) > 0
        post_cards_exist = len(post_cards) > 0
        
        print(f"[LINKEDIN-DEBUG] Page check: title='{page_title}', job_cards={len(job_cards)}, post_cards={len(post_cards)}, url={driver.current_url}")
        
        # Check for security issues
        security_issues = bool(re.search(r"security check|captcha|you are blocked|access denied|log in|sign in", f"{page_title} {body_text}", re.I))
        
        # If we have any cards, we're not blocked
        if job_cards_exist or post_cards_exist:
            return False
            
        # If we have security issues and no cards, we're blocked
        return security_issues
    except WebDriverException as e:
        print(f"[LINKEDIN-DEBUG] Exception in page_looks_blocked: {e}")
        return True


def wait_for_manual_verification(driver, source):
    if not source.get("waitForManualVerification") or source.get("headless"):
        return False

    # Extend timeout to 10 minutes (600,000ms = 600s) so you have plenty of time to log in
    timeout_seconds = int(source.get("manualVerificationTimeoutMs", 600000)) / 1000
    started_at = time.time()

    print(f"[LINKEDIN] ⏰ Waiting {int(timeout_seconds/60)} minutes for you to log in to LinkedIn in the Chrome window...")
    print("[LINKEDIN] 📝 Once you're successfully logged in and see LinkedIn's main feed/jobs page, the scraper will continue automatically.")
    print("[LINKEDIN] ⏳ Time remaining:")

    while time.time() - started_at < timeout_seconds:
        if not page_looks_blocked(driver):
            print("\n[LINKEDIN] ✅ Manual verification/login complete! Starting to scrape...")
            return True
        
        # Show time remaining every 10 seconds
        elapsed = time.time() - started_at
        remaining = int(timeout_seconds - elapsed)
        if remaining % 10 == 0:
            print(f"[LINKEDIN] ⏳ {remaining//60}m {remaining%60}s remaining to log in...")
            
        time.sleep(1)

    print("\n[LINKEDIN] ❌ Timeout reached - didn't detect successful login.")
    return False


def scroll_results(driver):
    # More robust scrolling to load all results
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(10):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)  # Wait for new content to load
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break  # No more content to load
        last_height = new_height
    print(f"[LINKEDIN-DEBUG] Scrolled through {last_height}px of page content")


# --- Core utility functions ---
def detect_country(text, config):
    """Simple country detection from text - matches country names in config"""
    # Ensure we always work with a string to prevent attribute errors
    if not text or not isinstance(text, str):
        return ""
    # Get countries from config, ensure it's always a list to prevent iteration errors
    countries = config.get("countries", ["United States", "Canada", "UK", "Australia", "India", "Germany"])
    # If countries is not a list (e.g. it's a dict), use the default list instead
    if not isinstance(countries, list):
        countries = ["United States", "Canada", "UK", "Australia", "India", "Germany"]
    for country in countries:
        # Ensure country is always a string before calling lower()
        if isinstance(country, str) and country.lower() in text.lower():
            return country
    return ""


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
    
    # Add freshness bonus based on how recent the job is
    posted_at_raw = job.get("postedAtRaw", "")
    # Ensure it's a string before calling lower() to avoid errors
    if isinstance(posted_at_raw, str) and posted_at_raw:
        posted_raw = posted_at_raw.lower()
        # Higher bonus for very fresh leads
        if "minute" in posted_raw:  # Posted within the last hour (minutes ago)
            base_score += 10
        elif "hour" in posted_raw:  # Posted within the last 24h
            # Extra bonus if it's < 10 hours old
            if any(str(h) in posted_raw for h in range(1, 10)):
                base_score += 8
            else:
                base_score += 5
        elif "today" in posted_raw:
            base_score += 7
        elif "day" in posted_raw and ("1" in posted_raw or "2" in posted_raw):  # Posted in last 48h
            base_score += 3
    
    # Add country detection bonus if we found a valid country
    if job.get("country"):
        base_score += 5
        
    # Critical fields are always highest - even with bonuses, cap base at 100
    return min(base_score, 100)


def is_current_linkedin_post(posted_at_raw):
    if not isinstance(posted_at_raw, str):
        return False

    posted_raw = posted_at_raw.lower().strip()
    if not posted_raw:
        return False

    current_markers = ("now", "just now", "minute", "min", "hour", "hr", "today")
    return any(marker in posted_raw for marker in current_markers)


# --- Free, reliable LinkedIn scraper with the same proven SeleniumBase setup as Upwork ---
def scrape_linkedin(source, category, config):
    all_jobs = []
    scraped_at = utc_now()
    max_results = int(source.get("maxResultsPerRun", 10))
    is_post_search = source.get("scrapePosts", False)
    seen_ids = set()  # Track seen lead IDs to prevent duplicates
    driver = None
    
    print(f"[LINKEDIN] Starting {'post' if is_post_search else 'job'} search for: {category['name']}")
    
    # Build the correct search URL
    if is_post_search:
        base_url = "https://www.linkedin.com/search/results/content/?"
    else:
        base_url = "https://www.linkedin.com/jobs/search/?"
    
    params = {
        "keywords": category["name"],
        "location": "United States, Canada"
    }
    # Use correct parameters for each search type
    if is_post_search:
        # Content/post searches require datePosted with quotes, manually build this part since urlencode will mess it up
        params["sortBy"] = "date_posted"
        params["origin"] = "FACETED_SEARCH"
        # First encode all standard params
        encoded_params = urlencode(params)
        # Manually add the quoted datePosted parameter which is required for LinkedIn's content search
        full_url = f"{base_url}{encoded_params}&datePosted=%22past-24h%22"
    else:
        # Job searches use f_TPR parameter (seconds since epoch format)
        params["f_TPR"] = "r86400"  # Last 24 hours for jobs
        # Use the official location parameter which is more reliable than geo IDs for US/Canada
        full_url = base_url + urlencode(params)
    print(f"[LINKEDIN] Search URL: {full_url}")
    
    try:
        # Create driver with the same proven setup as Upwork
        driver = create_driver(source)
        # First navigate to LinkedIn homepage to ensure we're properly authenticated (fixes content search page loading)
        print("[LINKEDIN-DEBUG] Navigating to LinkedIn homepage first to verify authentication...")
        driver.get("https://www.linkedin.com/")
        time.sleep(3)  # Wait to ensure we're logged in properly
        # Now navigate to our actual search URL
        driver.get(full_url)
        time.sleep(5)  # Increased wait time for page to load
        
        # Check if we're blocked - try automatic login first, then manual if that fails
        if page_looks_blocked(driver):
            # Try automatic login if credentials are provided
            if source.get("linkedin_email") and source.get("linkedin_password"):
                print("[LINKEDIN] Attempting automatic login with provided credentials...")
                try:
                    # Wait for login page to fully load
                    time.sleep(3)
                    
                    # Find username and password fields (LinkedIn uses these selectors)
                    email_field = driver.find_element("id", "username")
                    pass_field = driver.find_element("id", "password")
                    
                    # Enter credentials
                    email_field.send_keys(source["linkedin_email"])
                    time.sleep(1)
                    pass_field.send_keys(source["linkedin_password"])
                    time.sleep(1)
                    
                    # Click submit button
                    submit_button = driver.find_element("css selector", 'button[type="submit"]')
                    submit_button.click()
                    print("[LINKEDIN] Submitted login form, waiting for redirect...")
                    time.sleep(10)  # Wait for login to complete and redirect
                    
                    # Check if we're still blocked after login
                    if not page_looks_blocked(driver):
                        print("[LINKEDIN] ✅ Automatic login successful!")
                    else:
                        print("[LINKEDIN] Automatic login failed, falling back to manual verification...")
                        if not wait_for_manual_verification(driver, source):
                            save_debug(driver, category["name"])
                            print("[LINKEDIN] Blocked, couldn't recover with manual verification.")
                            return []
                except Exception as e:
                    print(f"[LINKEDIN] Automatic login error: {e}, falling back to manual...")
                    if not wait_for_manual_verification(driver, source):
                        save_debug(driver, category["name"])
                        print("[LINKEDIN] Blocked, couldn't recover with manual verification.")
                        return []
            else:
                # No credentials provided, use manual verification
                if not wait_for_manual_verification(driver, source):
                    save_debug(driver, category["name"])
                    print("[LINKEDIN] Blocked, couldn't recover with manual verification.")
                    return []
        
        # Scroll to load more results
        scroll_results(driver)
        time.sleep(2)
        
        # Save debug info
        save_debug(driver, category["name"], {"url": full_url})
        
        # Extra wait for post search pages since they take longer to render (LinkedIn content search is heavy JS)
        if is_post_search:
            print("[LINKEDIN-DEBUG] Waiting for post page to fully render with intelligent polling...")
            # Wait up to 20 seconds total for posts to appear, polling every 2.5s
            found_posts = False
            for attempt in range(8):  # 8 * 2.5s = 20s total
                # Check for ANY element that might be a post - LinkedIn's new UI uses dynamic class names, so look for all divs first
                all_divs = driver.find_elements("css selector", "div")
                # Print all classes that contain 'post' or 'update' to see what's actually there
                post_classes = []
                for elem in all_divs:
                    classes = elem.get_attribute("class")
                    if classes and ('post' in classes.lower() or 'update' in classes.lower()):
                        post_classes.append(classes)
                print(f"[LINKEDIN-DEBUG] All divs on page: {len(all_divs)} | Unique post/update classes: {list(set(post_classes))}")
                # Now find potential posts with a much broader selector
                potential_posts = driver.find_elements("css selector", "div")
                # Debug: print all elements on page to see what's actually there
                all_elements = driver.find_elements("css selector", "*")
                print(f"[LINKEDIN-DEBUG] Total elements on page: {len(all_elements)} | Potential LinkedIn posts: {len(potential_posts)}")
                # Print body HTML to see what content is actually rendered
                if attempt == 3:  # Print after 7.5s of waiting to see if anything loads
                    body_html = driver.find_element("tag name", "body").get_attribute("innerHTML")[:5000]
                    print(f"[LINKEDIN-DEBUG] First 5000 chars of body HTML: {body_html}")
                if len(potential_posts) > 0:
                    print(f"[LINKEDIN-DEBUG] Found {len(potential_posts)} potential posts after {attempt*2.5}s of waiting!")
                    found_posts = True
                    break
                print(f"[LINKEDIN-DEBUG] Wait attempt {attempt+1}/8: no posts found yet, waiting 2.5s and scrolling to trigger lazy loading...")
                # Scroll to trigger LinkedIn's infinite scroll/lazy loading
                driver.execute_script("window.scrollBy(0, 800);")
                time.sleep(2.5)
            if not found_posts:
                print("[LINKEDIN-DEBUG] WARNING: Never found any post elements after maximum wait time! LinkedIn may not have loaded the content.")
        
        # Debug: list all elements with data attributes to find correct post selectors
        if is_post_search:
            try:
                # Get page source and parse it to find all data- attributes
                page_source = driver.page_source
                # Find all elements with data- attributes using simple string search
                data_attrs_found = re.findall(r'data-[^\s>]+="[^"]+"', page_source)
                # Get unique data attribute names
                unique_data_attrs = set()
                for attr in data_attrs_found:
                    name = attr.split('=')[0]
                    unique_data_attrs.add(name)
                print(f"[LINKEDIN-DEBUG] All unique data- attributes found on page: {list(unique_data_attrs)[:30]}")
                
                # Find all post container classes
                class_matches = re.findall(r'class="([^"]+)"', page_source)
                post_classes = [cls for cls in class_matches if 'result' in cls.lower() or 'post' in cls.lower() or 'card' in cls.lower()]
                print(f"[LINKEDIN-DEBUG] Potential post container classes: {list(set(post_classes))[:20]}")
                
                # Count elements with search-result class
                result_elements = driver.find_elements("css selector", '[class*="search-result"]')
                print(f"[LINKEDIN-DEBUG] Total elements with 'search-result' in class: {len(result_elements)}")
            except Exception as e:
                print(f"[LINKEDIN-DEBUG] Error debugging post page elements: {e}")
                import traceback
                traceback.print_exc()
        
        if not is_post_search:
            # Find all job cards using Selenium (works with dynamically rendered content)
            job_cards = driver.find_elements("css selector", 'li.jobs-search-results__list-item, div[data-job-id], div[data-entity-id]')
            print(f"[LINKEDIN] Found {len(job_cards)} job cards on the page")
            
            for i, card in enumerate(job_cards):
                if len(all_jobs) >= max_results:
                    break
                try:
                    job_id = card.get_attribute("data-job-id") or card.get_attribute("data-entity-id") or f"linkedin-{time.time()}-{i}"
                    # Debug: log what's in the card to understand the structure
                    card_text = card.text
                    print(f"[LINKEDIN-DEBUG] Job card {i} full text: {card_text[:300]}...")
                    
                    # Extract data from card.text directly since it's reliably populated
                    lines = [line.strip() for line in card_text.split('\n') if line.strip()]
                    title = lines[0] if len(lines) > 0 else "Untitled Job"
                    company = lines[1] if len(lines) > 1 else ""
                    
                    # Find location and posted date by searching for common patterns
                    location = ""
                    posted_at_raw = "recently"
                    # Start from line 2 (third line) since lines[0] is title, lines[1] is company
                    for line in lines[2:]:
                        if any(kw in line.lower() for kw in ["ago", "hour", "minute", "day", "today", "promoted", "easy apply"]):
                            if any(kw in line.lower() for kw in ["ago", "hour", "minute", "day", "today"]):
                                posted_at_raw = line
                            break
                        elif line and not location:
                            location = line  # Capture the location line between company and posted date
                    
                    # Fallback to ensure we have something
                    title = clean_text(title)
                    company = clean_text(company)
                    location = clean_text(location)
                    posted_at_raw = clean_text(posted_at_raw)
                    
                    card_html = str(card)
                    email_matches = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", card_html)
                    # Improved phone regex that avoids matching random numbers from LinkedIn's HTML
                    phone_matches = re.findall(r"\+?\d{1,3}[-.\s]?\d{3,}[-.\s]?\d{3,}[-.\s]?\d{0,4}", card_html)
                    # Filter out any matches that are clearly not phone numbers (too long, contain HTML, etc.)
                    valid_phones = []
                    for p in phone_matches:
                        digit_count = sum(c.isdigit() for c in p)
                        if 10 <= digit_count <= 15:  # Valid phone numbers are 10-15 digits
                            valid_phones.append(p)
                    # Only set phone if we actually found a valid one
                    phone = valid_phones[0] if valid_phones else ""
                    
                    lead = {
                        "source": source["name"],
                        "sourceLeadId": job_id if job_id else f"linkedin-{time.time()}-{i}",
                        "url": f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else full_url,
                        "postedAt": scraped_at.isoformat(),
                        "postedAtRaw": posted_at_raw,
                        "companyName": company,
                        "location": location,
                        "country": "",
                        "category": category["name"],
                        "scrapedAt": scraped_at.isoformat(),
                        "email": email_matches[0] if email_matches else "",
                        "phone": phone,
                        "description": "",
                        "title": title
                    }
                    
                    # Debug the values before filtering
                    print(f"[LINKEDIN-DEBUG] Card {i} parsed: title='{title}', company='{company}', location='{location}', posted='{posted_at_raw}' (type: {type(posted_at_raw)})")
                    
                    # Filter to only keep jobs posted in the last 24h (matches our search filter)
                    if isinstance(posted_at_raw, str) and any(kw in posted_at_raw.lower() for kw in ["hour", "minute", "today", "1d", "recently"]):
                        # Ensure we always pass a string to detect_country to avoid the dict error
                        location_str = lead["location"] if isinstance(lead["location"], str) else ""
                        title_str = lead["title"] if isinstance(lead["title"], str) else ""
                        lead["country"] = detect_country(f"{location_str} {title_str}", config)
                        try:
                            lead["score"] = calculate_base_score(lead)
                            # Prevent duplicate leads by checking if we've already seen this ID
                            if lead["sourceLeadId"] not in seen_ids:
                                seen_ids.add(lead["sourceLeadId"])
                                all_jobs.append(lead)
                                print(f"[LINKEDIN] Added job lead: {lead['title']} at {lead['companyName']} in {lead['location']} (posted: {posted_at_raw}, score: {lead['score']})")
                            else:
                                print(f"[LINKEDIN-DEBUG] Skipped duplicate job lead: {lead['title']} (ID: {lead['sourceLeadId']})")
                        except Exception as score_err:
                            print(f"[LINKEDIN-DEBUG] Score calculation failed for card {i}: {str(score_err)}, lead dict: {lead}")
                    else:
                        print(f"[LINKEDIN-DEBUG] Skipping old job: {title} (posted: {posted_at_raw}) - too old for 24h filter or invalid type")
                    
                except Exception as e:
                    import traceback
                    print(f"[LINKEDIN-DEBUG] Failed to extract job card {i}: {str(e)}")
                    print(f"[LINKEDIN-DEBUG] Traceback: {traceback.format_exc()}")
                    continue
        
        else:
            # Parse posts from search results - using Selenium for dynamically rendered content
            # Correct selectors for LinkedIn's content search results page (all result items use these classes/data attrs)
            post_cards = driver.find_elements("css selector", 'li.search-results__list-item, div[data-search-result-id], div[data-entity-id], [class*="search-result-container"]')
            print(f"[LINKEDIN] Found {len(post_cards)} post cards on the page")
            # Debug: list all classes found on potential post containers
            if len(post_cards) == 0:
                all_elements = driver.find_elements("css selector", '*')
                post_classes = []
                all_classes = []
                for elem in all_elements[:100]:  # Check first 100 elements
                    cls = elem.get_attribute("class")
                    if cls:
                        all_classes.append(cls)
                        if any(kw in cls.lower() for kw in ["post", "update", "card", "feed", "result", "container"]):
                            post_classes.append(cls)
                print(f"[LINKEDIN-DEBUG] Unique classes containing post/feed/result keywords: {list(set(post_classes))[:20]}")
                # Also check all unique data- attributes to understand page structure
                data_attrs = set()
                for elem in all_elements[:100]:
                    for attr in elem.get_property('attributes'):
                        if attr['name'].startswith('data-'):
                            data_attrs.add(attr['name'])
                print(f"[LINKEDIN-DEBUG] All unique data- attributes found on page: {list(data_attrs)[:30]}")
            
            for i, card in enumerate(post_cards):
                if len(all_jobs) >= max_results:
                    break
                try:
                    # Get post ID from content search result attributes (matches job search structure)
                    post_id = card.get_attribute("data-search-result-id") or card.get_attribute("data-entity-id") or f"linkedin-post-{time.time()}-{i}"
                    
                    # Extract all text from the card first (works like job cards since content search results use similar layout)
                    card_text = card.text
                    print(f"[LINKEDIN-DEBUG] Post card {i} full text: {card_text[:300]}...")
                    
                    # Parse text like we do for job cards - it's much more reliable for content search
                    lines = [line.strip() for line in card_text.split('\n') if line.strip()]
                    title = lines[0] if len(lines) > 0 else "Untitled Post"
                    author = lines[1] if len(lines) > 1 else "Unknown Author"
                    
                    # Extract posted date like we do for jobs - find the line with time markers
                    posted_at_raw = "recently"
                    location = ""
                    for line in lines[2:]:
                        if any(kw in line.lower() for kw in ["ago", "hour", "minute", "day", "today"]):
                            posted_at_raw = line
                            break
                        elif line and not location:
                            location = line
                    
                    # Clean all extracted values
                    title = clean_text(title)
                    author = clean_text(author)
                    location = clean_text(location)
                    posted_at_raw = clean_text(posted_at_raw)
                    
                    card_html = str(card)
                    email_matches = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", card_html)
                    # Improved phone regex that avoids matching random numbers from LinkedIn's HTML
                    phone_matches = re.findall(r"\+?\d{1,3}[-.\s]?\d{3,}[-.\s]?\d{3,}[-.\s]?\d{0,4}", card_html)
                    # Filter out any matches that are clearly not phone numbers (too long, contain HTML, etc.)
                    valid_phones = []
                    for p in phone_matches:
                        digit_count = sum(c.isdigit() for c in p)
                        if 10 <= digit_count <= 15:  # Valid phone numbers are 10-15 digits
                            valid_phones.append(p)
                    # Only set phone if we actually found a valid one
                    phone = valid_phones[0] if valid_phones else ""
                    
                    lead = {
                        "source": source["name"],
                        "sourceLeadId": post_id if post_id else f"linkedin-post-{time.time()}-{i}",
                        "url": full_url,
                        "postedAt": scraped_at.isoformat(),
                        "postedAtRaw": posted_at_raw,
                        "companyName": author,
                        "location": location,
                        "country": "",
                        "category": category["name"],
                        "scrapedAt": scraped_at.isoformat(),
                        "email": email_matches[0] if email_matches else "",
                        "phone": phone,
                        "description": clean_text(card.text)[:500],
                        "title": f"LinkedIn Post: {author}"}
                    
                    # Debug post values
                    print(f"[LINKEDIN-DEBUG] Post card {i} parsed: author='{author}', posted='{posted_at_raw}' (type: {type(posted_at_raw)})")
                    
                    # Filter to only keep posts from the last 24h (matches our search filter)
                    if is_current_linkedin_post(posted_at_raw):
                        # Ensure we always pass a string to detect_country to avoid the dict error - include location like job posts
                        location_str = lead["location"] if isinstance(lead["location"], str) else ""
                        description_str = lead["description"] if isinstance(lead["description"], str) else ""
                        lead["country"] = detect_country(f"{location_str} {description_str}", config)
                        try:
                            lead["score"] = calculate_base_score(lead)
                            # Prevent duplicate leads by checking if we've already seen this ID
                            if lead["sourceLeadId"] not in seen_ids:
                                seen_ids.add(lead["sourceLeadId"])
                                all_jobs.append(lead)
                                print(f"[LINKEDIN] Added post lead: {lead['title']} (posted: {posted_at_raw}, score: {lead['score']})")
                            else:
                                print(f"[LINKEDIN-DEBUG] Skipped duplicate post lead: {lead['title']} (ID: {lead['sourceLeadId']})")
                        except Exception as score_err:
                            print(f"[LINKEDIN-DEBUG] Score calculation failed for post {i}: {str(score_err)}, lead dict: {lead}")
                    else:
                        print(f"[LINKEDIN-DEBUG] Skipping old post: {author} (posted: {posted_at_raw}) - too old for 24h filter or invalid type")
                    
                except Exception as e:
                    print(f"[LINKEDIN-DEBUG] Failed to extract post card {i}: {str(e)}")
                    continue
        
        print(f"[LINKEDIN] Finished. Total leads found: {len(all_jobs)} for {category['name']}")
        return all_jobs[:max_results]
        
    except Exception as e:
        print(f"[LINKEDIN] ✗ Scraper failed: {str(e)}")
        return []
        
    finally:
        if driver:
            if source.get("keepBrowserOpen", False):
                print("[LINKEDIN] 🛡️ Chrome window will STAY OPEN indefinitely - close it manually when you're done.")
                # Keep the script alive so Chrome doesn't get destroyed
                try:
                    while True:
                        time.sleep(3600)
                except:
                    pass
            else:
                print("[LINKEDIN] Closing Chrome driver...")
                try:
                    driver.quit()
                except Exception as e:
                    print(f"[LINKEDIN-DEBUG] Error while quitting driver: {e}")