import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests

COMPANY_BOARDS_PATH = os.path.join(os.path.dirname(__file__), "company_boards.json")
GREENHOUSE_URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL_TEMPLATE = "https://api.lever.co/v0/postings/{token}?mode=json"
AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"
AMAZON_BASE_JOB_URL = "https://www.amazon.jobs"
MIN_AGE_HOURS = 0
MAX_AGE_HOURS = 24
# Matches "intern", "interns", "internship", "internships" as whole words —
# but not "internal", "international", "internet" (plain substring matching
# on "intern" false-positives on those; this regex requires the match to end
# at a word boundary, e.g. right after "intern" or right after "internship").
INTERN_PATTERN = re.compile(r"\bintern(s|ship|ships)?\b", re.IGNORECASE)


def _load_company_boards():
    with open(COMPANY_BOARDS_PATH) as f:
        return json.load(f)


def _matches_title(title):
    lowered = title.lower()
    return "marketing" in lowered and bool(INTERN_PATTERN.search(title))


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
            params={"base_query": "marketing intern", "result_limit": 50},
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

            listings.append({
                "id": f"amazon:{job_id}",
                "title": title,
                "company": "Amazon",
                "location": job.get("normalized_location", "Unknown location"),
                "redirect_url": AMAZON_BASE_JOB_URL + job_path,
                "created": created_at,
            })
        except Exception as error:
            print(f"🔴 Skipping malformed Amazon job: {error}")
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
        token = entry["token"]

        if platform == "greenhouse":
            listings.extend(_fetch_greenhouse(company, token, now))
        elif platform == "lever":
            listings.extend(_fetch_lever(company, token, now))
        elif platform == "amazon":
            listings.extend(_fetch_amazon(now))
        else:
            print(f"🔴 Unknown platform '{platform}' for {company}, skipping")

    return listings
