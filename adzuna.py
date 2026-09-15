import os
from datetime import datetime, timezone, timedelta

import requests

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/1"
SEARCH_QUERY = "marketing intern"
MAX_DAYS_OLD = 1
RESULTS_PER_PAGE = 50
MIN_AGE_HOURS = 0
MAX_AGE_HOURS = 24


def fetch_new_marketing_internships():
    """
    Queries Adzuna for marketing internship listings (US-wide) and returns
    only those whose `created` timestamp is within the last 24 hours.

    Returns a list of dicts: id, title, company, location, redirect_url, created.
    Raises requests.RequestException on network/API failure.
    """
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]

    response = requests.get(
        ADZUNA_BASE_URL,
        params={
            "app_id": app_id,
            "app_key": app_key,
            "what": SEARCH_QUERY,
            "sort_by": "date",
            "max_days_old": MAX_DAYS_OLD,
            "results_per_page": RESULTS_PER_PAGE,
            "content-type": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()

    now = datetime.now(timezone.utc)
    min_created = now - timedelta(hours=MAX_AGE_HOURS)
    max_created = now - timedelta(hours=MIN_AGE_HOURS)

    listings = []
    for result in payload.get("results", []):
        try:
            # Guard against missing id
            listing_id = result.get("id")
            if not listing_id:
                print(f"Skipping result with missing id: {result}")
                continue

            created_raw = result.get("created")
            if not created_raw:
                continue
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            if not (min_created <= created_at <= max_created):
                continue

            # Guard against None values for company and location
            company_obj = result.get("company")
            company_name = company_obj.get("display_name", "Unknown company") if company_obj else "Unknown company"

            location_obj = result.get("location")
            location_name = location_obj.get("display_name", "Unknown location") if location_obj else "Unknown location"

            listings.append({
                "id": str(listing_id),
                "title": result.get("title", "Untitled listing"),
                "company": company_name,
                "location": location_name,
                "redirect_url": result.get("redirect_url", ""),
                "created": created_at,
            })
        except Exception as e:
            print(f"Skipping malformed result: {result}. Error: {e}")
            continue

    return listings
