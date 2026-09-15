import os
from datetime import datetime, timezone, timedelta

import requests

ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/1"
SEARCH_QUERY = "marketing internship"
MAX_DAYS_OLD = 2
RESULTS_PER_PAGE = 50
MIN_AGE_HOURS = 24
MAX_AGE_HOURS = 48


def fetch_new_marketing_internships():
    """
    Queries Adzuna for marketing internship listings (US-wide) and returns
    only those whose `created` timestamp is between 24 and 48 hours old.

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
        created_raw = result.get("created")
        if not created_raw:
            continue
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if not (min_created <= created_at <= max_created):
            continue

        listings.append({
            "id": str(result["id"]),
            "title": result.get("title", "Untitled listing"),
            "company": result.get("company", {}).get("display_name", "Unknown company"),
            "location": result.get("location", {}).get("display_name", "Unknown location"),
            "redirect_url": result.get("redirect_url", ""),
            "created": created_at,
        })

    return listings
