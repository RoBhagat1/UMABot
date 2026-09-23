import json
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests

COMPANY_BOARDS_PATH = os.path.join(os.path.dirname(__file__), "company_boards.json")
GREENHOUSE_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL_TEMPLATE = "https://api.lever.co/v0/postings/{token}?mode=json"
AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"
AMAZON_BASE_JOB_URL = "https://www.amazon.jobs"
WORKABLE_URL_TEMPLATE = "https://apply.workable.com/api/v1/widget/accounts/{token}"
ASHBY_URL_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{token}"
# A handful of large employers (AMD, Keysight, JHU APL, Rivian, Garmin) run
# career sites on a common third-party career-site platform (fronting their
# actual ATS, e.g. iCIMS) that exposes this same JSON endpoint shape on the
# company's own custom domain.
CAREER_SITE_API_URL_TEMPLATE = "https://{host}/api/jobs?limit=100"
WORKDAY_SEARCH_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
WORKDAY_JOB_BASE_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/{site}"
# Workday only gives a coarse relative "posted" bucket, not an exact
# timestamp — this maps each bucket to an approximate age in hours so it
# can go through the same _in_window() check as every other platform.
WORKDAY_POSTED_AGE_HOURS = {
    "today": 0,
    "yesterday": 24,
}
MIN_AGE_HOURS = 0
MAX_AGE_HOURS = 24
# Matches "intern", "interns", "internship", "internships" as whole words —
# but not "internal", "international", "internet" (plain substring matching
# on "intern" false-positives on those; this regex requires the match to end
# at a word boundary, e.g. right after "intern" or right after "internship").
INTERN_PATTERN = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)
# New-grad-level roles never say "intern" — they use one of these instead.
NEW_GRAD_PATTERN = re.compile(
    r"\b(new grad(uate)?s?|recent grad(uate)?s?|early career|"
    r"entry[- ]level|class of 20\d{2}|university grad(uate)?s?|"
    r"campus hire)\b",
    re.IGNORECASE,
)
# Marketing-adjacent title keywords — a strict "marketing" only match misses
# real marketing-field roles titled things like "Communications Intern" or
# "Promotion & Publicity Intern" that don't literally say "marketing".
MARKETING_KEYWORDS_PATTERN = re.compile(
    r"\b(marketing|brand|branding|communications?|public relations|"
    r"social media|digital media|content|promotion|publicity|growth|"
    r"campaign|creative|advertising)\b",
    re.IGNORECASE,
)
PM_KEYWORDS_PATTERN = re.compile(
    r"\b(product manager|product management|product owner|"
    r"associate product manager|\bapm\b|technical product manager|"
    r"product analyst)\b",
    re.IGNORECASE,
)
DESIGN_KEYWORDS_PATTERN = re.compile(
    r"\b(design|designer|ux|ui|user experience|user interface)\b",
    re.IGNORECASE,
)

# Each category is a (field, level) pair. A title has to match both the
# field's keyword pattern and the level's pattern to count — e.g. a
# "marketing_intern" match needs a marketing-adjacent word AND "intern"
# somewhere in the title. Adding a new field or level means adding it to
# FIELD_PATTERNS/LEVEL_PATTERNS and one entry per new combination here — no
# schema change, since subscriptions are stored generically in
# internship_subscriptions keyed by each category's "key" string. Order
# matters: it's the order categories are asked about in the sequential
# opt-in DM flow (see handlers/internships.py).
FIELD_PATTERNS = {
    "marketing": MARKETING_KEYWORDS_PATTERN,
    "pm": PM_KEYWORDS_PATTERN,
    "design": DESIGN_KEYWORDS_PATTERN,
}
LEVEL_PATTERNS = {
    "intern": INTERN_PATTERN,
    "newgrad": NEW_GRAD_PATTERN,
}
CATEGORIES = [
    {
        "key": "marketing_intern",
        "label": "marketing intern",
        "field": "marketing",
        "level": "intern",
        "ask_text": "Want daily alerts about new marketing internships (posted in the last 24 hours)?",
    },
    {
        "key": "marketing_newgrad",
        "label": "marketing new grad",
        "field": "marketing",
        "level": "newgrad",
        "ask_text": "Want daily alerts about new marketing new-grad roles (posted in the last 24 hours)?",
    },
    {
        "key": "pm_intern",
        "label": "product management intern",
        "field": "pm",
        "level": "intern",
        "ask_text": "Want daily alerts about new product management internships (posted in the last 24 hours)?",
    },
    {
        "key": "pm_newgrad",
        "label": "product management new grad",
        "field": "pm",
        "level": "newgrad",
        "ask_text": "Want daily alerts about new product management new-grad roles (posted in the last 24 hours)?",
    },
    {
        "key": "design_intern",
        "label": "design intern",
        "field": "design",
        "level": "intern",
        "ask_text": "Want daily alerts about new design internships (posted in the last 24 hours)?",
    },
    {
        "key": "design_newgrad",
        "label": "design new grad",
        "field": "design",
        "level": "newgrad",
        "ask_text": "Want daily alerts about new design new-grad roles (posted in the last 24 hours)?",
    },
]
CATEGORY_KEYS = [c["key"] for c in CATEGORIES]

US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
}
US_INDICATOR_PATTERN = re.compile(r"\b(usa|u\.s\.a\.?|united states|u\.s\.)\b", re.IGNORECASE)
US_STATE_SUFFIX_PATTERN = re.compile(
    r",\s*(" + "|".join(US_STATE_ABBREVIATIONS) + r")\b"
)
NON_US_COUNTRY_PATTERN = re.compile(
    r"\b("
    r"canada|mexico|united kingdom|england|scotland|wales|\buk\b|ireland|"
    r"france|germany|spain|italy|netherlands|belgium|switzerland|austria|"
    r"poland|portugal|sweden|norway|denmark|finland|czech|hungary|romania|"
    r"greece|india|china|japan|korea|singapore|malaysia|philippines|"
    r"indonesia|vietnam|thailand|hong kong|taiwan|australia|new zealand|"
    r"brazil|argentina|chile|colombia|peru|south africa|nigeria|kenya|"
    r"\buae\b|dubai|saudi|israel|turkey|russia|ukraine"
    r")\b",
    re.IGNORECASE,
)


def _is_us_location(location):
    """
    Heuristic US-location filter based on free-text location strings, since
    the platforms don't consistently expose a clean structured country
    field. Defaults to excluding ambiguous/ungeocodable text (e.g. bare
    "Remote" with no country hint) rather than including it — erring
    toward under- rather than over-inclusion, since the requirement is
    strictly US-only.
    """
    if not location:
        return False
    if NON_US_COUNTRY_PATTERN.search(location):
        return False
    if US_INDICATOR_PATTERN.search(location):
        return True
    if US_STATE_SUFFIX_PATTERN.search(location):
        return True
    lowered = location.lower()
    if any(state in lowered for state in US_STATE_NAMES):
        return True
    if re.search(r"remote.*\bus\b", lowered):
        return True
    return False


def _load_company_boards():
    with open(COMPANY_BOARDS_PATH) as f:
        return json.load(f)


def _matched_categories(title):
    """
    Returns the list of category keys whose (field, level) pair both match
    this title. A title can match more than one category — e.g. "Product
    Marketing Intern" matches both "marketing_intern" and "pm_intern"; a
    title can't match both an intern and a new-grad category unless it
    genuinely mentions both (rare, harmless if it does).
    """
    matched = []
    for cat in CATEGORIES:
        if FIELD_PATTERNS[cat["field"]].search(title) and LEVEL_PATTERNS[cat["level"]].search(title):
            matched.append(cat["key"])
    return matched


def _in_window(created_at, now):
    min_created = now - timedelta(hours=MAX_AGE_HOURS)
    max_created = now - timedelta(hours=MIN_AGE_HOURS)
    return min_created <= created_at <= max_created


def _fetch_greenhouse(company, token, now):
    listings = []
    try:
        response = requests.get(GREENHOUSE_URL_TEMPLATE.format(token=token), timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Greenhouse board for {company} ({token}): {error}")
        return listings

    for job in payload.get("jobs", []):
        try:
            title = job.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            published_raw = job.get("first_published")
            if not published_raw:
                continue
            created_at = datetime.fromisoformat(published_raw)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at = created_at.astimezone(timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_id = job.get("id")
            if not job_id:
                continue

            location_obj = job.get("location")
            location_name = location_obj.get("name", "Unknown location") if location_obj else "Unknown location"
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"gh:{token}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job.get("absolute_url", ""),
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Greenhouse job at {company} ({token}): {error}")
            continue

    return listings


def _fetch_lever(company, token, now):
    listings = []
    try:
        response = requests.get(LEVER_URL_TEMPLATE.format(token=token), timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Lever board for {company} ({token}): {error}")
        return listings

    if not isinstance(payload, list):
        print(f"🔴 Unexpected Lever response shape for {company} ({token}): {payload}")
        return listings

    for job in payload:
        try:
            title = job.get("text", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            created_ms = job.get("createdAt")
            if not created_ms:
                continue
            created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_id = job.get("id")
            if not job_id:
                continue

            lever_categories = job.get("categories") or {}
            location_name = lever_categories.get("location", "Unknown location")
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"lever:{token}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job.get("hostedUrl", ""),
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Lever job at {company} ({token}): {error}")
            continue

    return listings


def _fetch_amazon(now):
    listings = []
    # Amazon's search is a server-side keyword filter, unlike every other
    # platform here which returns its full job list for client-side
    # filtering — so it has to be queried once per level (intern vs new
    # grad) to avoid a single "intern" query silently excluding new-grad
    # postings that never say "intern". Results are deduped by job id.
    seen_job_ids = set()
    all_jobs = []
    for query in ("intern", "new grad"):
        try:
            response = requests.get(
                AMAZON_SEARCH_URL,
                params={"base_query": query, "result_limit": 50, "country": "USA"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            print(f"🔴 Error fetching Amazon job board (query='{query}'): {error}")
            continue

        for job in payload.get("jobs", []):
            job_id = job.get("id")
            if job_id and job_id not in seen_job_ids:
                seen_job_ids.add(job_id)
                all_jobs.append(job)

    for job in all_jobs:
        try:
            title = job.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            posted_raw = job.get("posted_date")
            if not posted_raw:
                continue
            # Amazon gives a date only (no time), e.g. "September 14, 2026" —
            # treat it as midnight UTC. This means our 24h window has up to
            # a day of imprecision for Amazon listings specifically, since
            # we don't know the actual time of day they posted.
            created_at = datetime.strptime(posted_raw, "%B %d, %Y").replace(tzinfo=timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_path = job.get("job_path")
            job_id = job.get("id")
            if not job_id or not job_path:
                continue

            location_name = job.get("normalized_location", "Unknown location")
            country_code = job.get("country_code")
            is_us = country_code == "USA" if country_code else _is_us_location(location_name)
            if not is_us:
                continue

            listings.append({
                "id": f"amazon:{job_id}",
                "title": title,
                "company": "Amazon",
                "location": location_name,
                "redirect_url": AMAZON_BASE_JOB_URL + job_path,
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Amazon job: {error}")
            continue

    return listings


def _fetch_workable(company, token, now):
    listings = []
    try:
        response = requests.get(
            WORKABLE_URL_TEMPLATE.format(token=quote(token, safe="")),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Workable board for {company} ({token}): {error}")
        return listings

    for job in payload.get("jobs", []):
        try:
            title = job.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            created_raw = job.get("published_on") or job.get("created_at")
            if not created_raw:
                continue
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at = created_at.astimezone(timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_shortcode = job.get("shortcode") or job.get("id")
            job_url = job.get("url") or job.get("application_url")
            if not job_shortcode or not job_url:
                continue

            location_name = (
                job.get("location", {}).get("location_str", "Unknown location")
                if isinstance(job.get("location"), dict) else "Unknown location"
            )
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"workable:{token}:{job_shortcode}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job_url,
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Workable job at {company} ({token}): {error}")
            continue

    return listings


def _fetch_ashby(company, token, now):
    listings = []
    try:
        response = requests.get(
            ASHBY_URL_TEMPLATE.format(token=quote(token, safe="")),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Ashby board for {company} ({token}): {error}")
        return listings

    for job in payload.get("jobs", []):
        try:
            title = job.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            published_raw = job.get("publishedAt")
            if not published_raw:
                continue
            created_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at = created_at.astimezone(timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_id = job.get("id")
            job_url = job.get("jobUrl") or job.get("applyUrl")
            if not job_id or not job_url:
                continue

            location_name = job.get("location", "Unknown location")
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"ashby:{token}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job_url,
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Ashby job at {company} ({token}): {error}")
            continue

    return listings


def _fetch_career_site_api(company, host, now):
    listings = []
    try:
        response = requests.get(
            CAREER_SITE_API_URL_TEMPLATE.format(host=host),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching career-site-api board for {company} ({host}): {error}")
        return listings

    # Results come back sorted newest-first; the top 100 comfortably covers
    # the 24h window for every company observed on this platform so far.
    for job in payload.get("jobs", []):
        try:
            data = job.get("data", {})
            title = data.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            posted_raw = data.get("posted_date")
            if not posted_raw:
                continue
            created_at = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at = created_at.astimezone(timezone.utc)
            if not _in_window(created_at, now):
                continue

            job_id = data.get("req_id")
            job_url = data.get("apply_url")
            if not job_id or not job_url:
                continue

            location_name = data.get("full_location") or ", ".join(
                part for part in [data.get("city"), data.get("state"), data.get("country")] if part
            )
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"career_site_api:{host}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job_url,
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed career-site-api job at {company} ({host}): {error}")
            continue

    return listings


def _parse_workday_posted_age_hours(posted_on):
    # e.g. "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    lowered = posted_on.lower().replace("posted", "").strip()
    if lowered in WORKDAY_POSTED_AGE_HOURS:
        return WORKDAY_POSTED_AGE_HOURS[lowered]
    match = re.match(r"(\d+)\+?\s*days?\s*ago", lowered)
    if match:
        return int(match.group(1)) * 24
    return None


def _fetch_workday(company, tenant, dc, site, now):
    listings = []
    # Like Amazon, Workday's searchText is a server-side keyword filter, so
    # it's queried once per level to avoid excluding new-grad postings that
    # never say "intern". Results are deduped by external path.
    seen_paths = set()
    all_jobs = []
    for query in ("intern", "new grad"):
        try:
            response = requests.post(
                WORKDAY_SEARCH_URL_TEMPLATE.format(tenant=tenant, dc=dc, site=site),
                json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": query},
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            print(f"🔴 Error fetching Workday board for {company} ({tenant}, query='{query}'): {error}")
            continue

        for job in payload.get("jobPostings", []):
            path = job.get("externalPath")
            if path and path not in seen_paths:
                seen_paths.add(path)
                all_jobs.append(job)

    for job in all_jobs:
        try:
            title = job.get("title", "")
            categories = _matched_categories(title)
            if not categories:
                continue

            posted_on = job.get("postedOn")
            if not posted_on:
                continue
            age_hours = _parse_workday_posted_age_hours(posted_on)
            if age_hours is None:
                continue
            # Approximate: Workday only gives a coarse bucket, not an exact
            # timestamp, so this is a best-effort estimate within that bucket.
            created_at = now - timedelta(hours=age_hours)
            if not _in_window(created_at, now):
                continue

            external_path = job.get("externalPath")
            bullet_fields = job.get("bulletFields") or []
            job_id = bullet_fields[0] if bullet_fields else external_path
            if not job_id or not external_path:
                continue

            location_name = job.get("locationsText", "Unknown location")
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"workday:{tenant}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": WORKDAY_JOB_BASE_URL_TEMPLATE.format(tenant=tenant, dc=dc, site=site) + external_path,
                "created": created_at,
                "categories": categories,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Workday job at {company} ({tenant}): {error}")
            continue

    return listings


def fetch_new_internships():
    """
    Polls every company board in company_boards.json once, and returns every
    internship posting from the last 24 hours whose title matches at least
    one entry in CATEGORIES. Each listing is tagged with every category key
    it matches (e.g. "Product Marketing Intern" tags as both "marketing"
    and "pm"), so callers can filter per-subscriber without re-scanning.

    Returns a list of dicts: id, title, company, location, redirect_url,
    created, categories. A single company's fetch failure is logged and
    skipped — it does not abort the scan of the remaining companies.
    """
    now = datetime.now(timezone.utc)
    companies = _load_company_boards()

    listings = []
    for entry in companies:
        company = entry["company"]
        platform = entry["platform"]

        if platform == "greenhouse":
            listings.extend(_fetch_greenhouse(company, entry["token"], now))
        elif platform == "lever":
            listings.extend(_fetch_lever(company, entry["token"], now))
        elif platform == "amazon":
            listings.extend(_fetch_amazon(now))
        elif platform == "workable":
            listings.extend(_fetch_workable(company, entry["token"], now))
        elif platform == "ashby":
            listings.extend(_fetch_ashby(company, entry["token"], now))
        elif platform == "workday":
            listings.extend(_fetch_workday(company, entry["tenant"], entry["dc"], entry["site"], now))
        elif platform == "career_site_api":
            listings.extend(_fetch_career_site_api(company, entry["host"], now))
        else:
            print(f"🔴 Unknown platform '{platform}' for {company}, skipping")

    return listings
