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
# Marketing-adjacent title keywords — a strict "marketing" only match misses
# real marketing-field roles titled things like "Communications Intern" or
# "Promotion & Publicity Intern" that don't literally say "marketing".
MARKETING_KEYWORDS_PATTERN = re.compile(
    r"\b(marketing|brand|branding|communications?|public relations|"
    r"social media|digital media|content|promotion|publicity|growth|"
    r"campaign|creative|advertising)\b",
    re.IGNORECASE,
)

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


def _matches_title(title):
    return bool(MARKETING_KEYWORDS_PATTERN.search(title)) and bool(INTERN_PATTERN.search(title))


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
            if not _matches_title(title):
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
            if not _matches_title(title):
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

            categories = job.get("categories") or {}
            location_name = categories.get("location", "Unknown location")
            if not _is_us_location(location_name):
                continue

            listings.append({
                "id": f"lever:{token}:{job_id}",
                "title": title,
                "company": company,
                "location": location_name,
                "redirect_url": job.get("hostedUrl", ""),
                "created": created_at,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Lever job at {company} ({token}): {error}")
            continue

    return listings


def _fetch_amazon(now):
    listings = []
    try:
        response = requests.get(
            AMAZON_SEARCH_URL,
            params={"base_query": "marketing intern", "result_limit": 50, "country": "USA"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Amazon job board: {error}")
        return listings

    for job in payload.get("jobs", []):
        try:
            title = job.get("title", "")
            if not _matches_title(title):
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
            if not _matches_title(title):
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
            if not _matches_title(title):
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
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Ashby job at {company} ({token}): {error}")
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
    try:
        response = requests.post(
            WORKDAY_SEARCH_URL_TEMPLATE.format(tenant=tenant, dc=dc, site=site),
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "marketing intern"},
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"🔴 Error fetching Workday board for {company} ({tenant}): {error}")
        return listings

    for job in payload.get("jobPostings", []):
        try:
            title = job.get("title", "")
            if not _matches_title(title):
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
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Workday job at {company} ({tenant}): {error}")
            continue

    return listings


def fetch_new_marketing_internships():
    """
    Polls every company's Greenhouse/Lever board in company_boards.json for
    job titles matching "marketing" and "intern", posted within the last 24
    hours. Returns direct employer application links (no redirect/tracking
    chain), unlike the previous Adzuna-based aggregator approach.

    Returns a list of dicts: id, title, company, location, redirect_url, created.
    A single company's fetch failure is logged and skipped — it does not
    abort the scan of the remaining companies.
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
        else:
            print(f"🔴 Unknown platform '{platform}' for {company}, skipping")

    return listings
