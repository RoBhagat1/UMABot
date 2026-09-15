# Marketing Internship Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let opted-in Slack members receive a daily DM digest of new marketing internship postings (24-48 hours old) sourced from the Adzuna job search API.

**Architecture:** A new `adzuna.py` module handles the external API call and time-window filtering in isolation. `handlers/internships.py` implements the opt-in (`internship ask`) and self-serve `internship unsubscribe` Slack commands, following the exact same admin-broadcast-DM pattern as the existing `handlers/birthday.py`. A new `internship_digest_job()` in `jobs.py`, scheduled at noon Pacific via the existing APScheduler instance, ties it together: fetch → dedup against a `sent_internship_listings` table → DM all `subscribed` users.

**Tech Stack:** Python 3.11, slack_bolt, APScheduler, psycopg2 (Postgres), `requests` (already a dependency) for the Adzuna HTTP call. No new pip dependencies required.

**Spec:** `docs/superpowers/specs/2026-09-15-internship-alerts-design.md`

## Global Constraints

- Adzuna search: US-wide, query `"marketing internship"`, filtered to listings whose `created` timestamp is between 24 and 48 hours old at job-run time.
- Daily job runs at **12:00 PM Pacific** (`hour=12, minute=0` in the existing `America/Los_Angeles`-timezone APScheduler instance).
- A day with zero new qualifying listings sends **no DMs** — silence, not a "nothing today" message.
- `internship ask` is **admin-only** (`ADMIN_USER_ID` from `config.py`), mirrors `birthday setup`'s skip-known-users broadcast behavior exactly.
- `internship unsubscribe` is self-serve, any user, any channel/DM.
- No automated test suite exists in this codebase (confirmed: no `pytest`, no `tests/` directory). Every existing feature (birthday, spot, assassin) has been verified via manual scripts run against the real Postgres DB and real Slack API, not unit tests. This plan follows that same convention — each task's "test" step is a concrete, runnable manual verification, not a pytest suite.
- `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are required env vars, read directly via `os.environ` (matching how `DATABASE_URL` and `SLACK_BOT_TOKEN` are read elsewhere in this codebase) — not stored in `config.py`.

---

### Task 1: Adzuna developer account + database schema

**Files:**
- Modify: `database.py:5-79` (add two new `CREATE TABLE` commands inside `setup_database()`)

**Interfaces:**
- Produces: two new tables — `internship_subscribers (user_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK (status IN ('subscribed','declined')), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())` and `sent_internship_listings (listing_id TEXT PRIMARY KEY, sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`. Later tasks read/write these by name.

- [ ] **Step 1: Sign up for a free Adzuna developer account**

Go to https://developer.adzuna.com/, sign up, and register an app to get an `app_id` and `app_key`. Add them to `.env` (this file is gitignored, never commit it):

```
ADZUNA_APP_ID=your_app_id_here
ADZUNA_APP_KEY=your_app_key_here
```

- [ ] **Step 2: Add the two new table definitions to `database.py`**

In `database.py`, add these two new command strings right after the existing `birthdays_table_command` (currently ending at line 56):

```python
    internship_subscribers_table_command = """
    CREATE TABLE IF NOT EXISTS internship_subscribers (
        user_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('subscribed', 'declined')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    sent_internship_listings_table_command = """
    CREATE TABLE IF NOT EXISTS sent_internship_listings (
        listing_id TEXT PRIMARY KEY,
        sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
```

Then add two more `cur.execute(...)` calls right after the existing `cur.execute(birthdays_table_command)` line (currently line 70), before `conn.commit()`:

```python
        cur.execute(internship_subscribers_table_command)
        cur.execute(sent_internship_listings_table_command)
```

- [ ] **Step 3: Verify the tables get created**

Run (from the project root, with the venv active and `.env` loaded):

```bash
source venv/bin/activate
set -a; source .env; set +a
python3 -c "from database import setup_database; setup_database()"
```

Expected output includes `✅ All database tables are ready.` with no errors.

Then confirm both new tables exist:

```bash
python3 - <<'EOF'
import os, psycopg2
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_name IN ('internship_subscribers', 'sent_internship_listings')
""")
print(sorted(row[0] for row in cur.fetchall()))
EOF
```

Expected output: `['internship_subscribers', 'sent_internship_listings']`

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "Add internship_subscribers and sent_internship_listings tables

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Adzuna API client module

**Files:**
- Create: `adzuna.py`

**Interfaces:**
- Consumes: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` env vars (set in Task 1, Step 1).
- Produces: `fetch_new_marketing_internships() -> list[dict]`, where each dict has keys `id` (str), `title` (str), `company` (str), `location` (str), `redirect_url` (str), `created` (`datetime`, UTC-aware). Raises on network/HTTP failure (caller's responsibility to catch — see Task 4).

- [ ] **Step 1: Write `adzuna.py`**

```python
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
```

- [ ] **Step 2: Verify it fetches real data**

```bash
source venv/bin/activate
set -a; source .env; set +a
python3 -c "
from adzuna import fetch_new_marketing_internships
listings = fetch_new_marketing_internships()
print(f'Found {len(listings)} listings in the 24-48h window')
for l in listings[:3]:
    print(l['title'], '-', l['company'], '-', l['created'])
"
```

Expected: no exception; prints a count (may legitimately be 0 if nothing in that window right now) and up to 3 sample listings with a `created` timestamp visibly between 24-48 hours before your current time.

If you want to sanity-check the filter logic itself without waiting for real listings to land in the window, temporarily widen `MIN_AGE_HOURS`/`MAX_AGE_HOURS` in a copy of the call (e.g. 0 to 72) in a scratch script — do not leave this change in `adzuna.py`.

- [ ] **Step 3: Commit**

```bash
git add adzuna.py
git commit -m "Add Adzuna API client for marketing internship search

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Opt-in (`internship ask`) and unsubscribe commands

**Files:**
- Create: `handlers/internships.py`

**Interfaces:**
- Consumes: `app`, `BOT_USER_ID` from `bot.py`; `ADMIN_USER_ID` from `config.py`; `internship_subscribers` table from Task 1.
- Produces: registers Slack message listeners on import (same side-effecting-import pattern as `handlers/birthday.py`); no functions consumed by other tasks directly (the digest job in Task 4 only reads the `internship_subscribers` table, not this module).

- [ ] **Step 1: Write `handlers/internships.py`**

```python
import os
import re
import psycopg2

from bot import app, BOT_USER_ID
from config import ADMIN_USER_ID

YES_PATTERN = re.compile(r"^\s*y(es)?\s*$", re.IGNORECASE)
NO_PATTERN = re.compile(r"^\s*n(o)?\s*$", re.IGNORECASE)


def _all_workspace_user_ids(client):
    members = []
    cursor = None
    while True:
        response = client.users_list(cursor=cursor, limit=200)
        members.extend(response['members'])
        cursor = response.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
    return [
        member['id'] for member in members
        if not member.get('is_bot') and not member.get('deleted') and member['id'] != 'USLACKBOT'
    ]


def handle_internship_ask_command(event, say, client, target_user_ids=None):
    if event['user'] != ADMIN_USER_ID:
        say("Sorry, only the designated admin can run internship ask.")
        return

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM internship_subscribers")
        known_user_ids = {row[0] for row in cur.fetchall()}

        if target_user_ids:
            candidate_ids = target_user_ids
        else:
            candidate_ids = _all_workspace_user_ids(client)

        dm_count = 0
        for user_id in candidate_ids:
            if user_id in known_user_ids:
                continue
            try:
                dm = client.conversations_open(users=user_id)
                dm_channel_id = dm['channel']['id']
                client.chat_postMessage(
                    channel=dm_channel_id,
                    text="📋 Want daily alerts about new marketing internships "
                         "(posted 24-48 hours ago)? Reply here with yes or no."
                )
                dm_count += 1
            except Exception as dm_error:
                print(f"🔴 Error DMing {user_id} for internship ask: {dm_error}")

        scope = f"{len(candidate_ids)} selected user(s)" if target_user_ids else "the workspace"
        say(f"Internship ask complete. DMed {dm_count} user(s) from {scope} who hadn't answered yet.")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in handle_internship_ask_command: {error}")
        say("Sorry, something went wrong running internship ask.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.message(re.compile(r"^internship ask", re.IGNORECASE))
def handle_internship_ask_message(message, say, client):
    target_user_ids = [
        user_id for user_id in re.findall(r"<@(\w+)>", message['text'])
        if user_id != BOT_USER_ID
    ]
    handle_internship_ask_command(message, say, client, target_user_ids=target_user_ids or None)


def _set_subscription_status(user_id, status):
    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO internship_subscribers (user_id, status)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET status = EXCLUDED.status
            """,
            (user_id, status)
        )
        conn.commit()
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def is_internship_reply(message):
    if message.get('channel_type') != 'im' or 'text' not in message or 'bot_id' in message:
        return False
    text = message['text']
    return bool(YES_PATTERN.match(text) or NO_PATTERN.match(text))


@app.message(matchers=[is_internship_reply])
def handle_internship_reply(message, say):
    user_id = message['user']
    text = message['text']
    status = "subscribed" if YES_PATTERN.match(text) else "declined"
    try:
        _set_subscription_status(user_id, status)
        if status == "subscribed":
            say("You're subscribed! I'll DM you when new marketing internships show up. 🎉")
        else:
            say("Got it, you won't receive internship alerts.")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error saving internship subscription for {user_id}: {error}")
        say("Sorry, something went wrong saving your answer. Please try again.")


@app.message(re.compile(r"^internship unsubscribe\s*$", re.IGNORECASE))
def handle_internship_unsubscribe(message, say):
    user_id = message['user']
    try:
        _set_subscription_status(user_id, "declined")
        say("You've been unsubscribed from internship alerts.")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error unsubscribing {user_id} from internships: {error}")
        say("Sorry, something went wrong unsubscribing you. Please try again.")
```

- [ ] **Step 2: Verify syntax**

```bash
source venv/bin/activate
python3 -c "import ast; ast.parse(open('handlers/internships.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 3: Verify the opt-in status write path directly against the DB** (without needing the bot running yet)

```bash
set -a; source .env; set +a
python3 -c "
from handlers.internships import _set_subscription_status
import os, psycopg2

test_user = 'TEST_USER_PLAN_VERIFICATION'
_set_subscription_status(test_user, 'subscribed')

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT status FROM internship_subscribers WHERE user_id = %s', (test_user,))
print('after subscribe:', cur.fetchone())

_set_subscription_status(test_user, 'declined')
cur.execute('SELECT status FROM internship_subscribers WHERE user_id = %s', (test_user,))
print('after decline:', cur.fetchone())

cur.execute('DELETE FROM internship_subscribers WHERE user_id = %s', (test_user,))
conn.commit()
print('cleaned up test row')
"
```

Expected output:
```
after subscribe: ('subscribed',)
after decline: ('declined',)
cleaned up test row
```

This confirms the upsert (insert-then-update-on-conflict) logic works correctly before wiring it into live Slack commands.

- [ ] **Step 4: Commit**

```bash
git add handlers/internships.py
git commit -m "Add internship ask and unsubscribe Slack commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Daily digest job

**Files:**
- Modify: `jobs.py:1-9` (imports), append new function after `birthday_job` (currently ending at `jobs.py:103`)

**Interfaces:**
- Consumes: `fetch_new_marketing_internships()` from Task 2 (`adzuna.py`); `internship_subscribers` and `sent_internship_listings` tables from Task 1; `app.client` from `bot.py` (already imported in `jobs.py`).
- Produces: `internship_digest_job()` — no args, no return value. Consumed by `app.py` in Task 5.

- [ ] **Step 1: Add the Adzuna import to `jobs.py`**

At the top of `jobs.py`, change:

```python
from bot import app
from config import daily_bonus_users, BIRTHDAY_CHANNEL_ID
from utils import get_user_name
```

to:

```python
from bot import app
from config import daily_bonus_users, BIRTHDAY_CHANNEL_ID
from utils import get_user_name
from adzuna import fetch_new_marketing_internships
```

- [ ] **Step 2: Append `internship_digest_job()` to the end of `jobs.py`**

```python


def internship_digest_job():
    print("--- Running Internship Digest Job ---")
    try:
        listings = fetch_new_marketing_internships()
    except Exception as error:
        print(f"🔴 Error fetching internship listings from Adzuna: {error}")
        return

    if not listings:
        print("--- No new qualifying internship listings found. Skipping. ---")
        return

    conn = None
    cur = None
    new_listings = []
    subscriber_ids = []
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT listing_id FROM sent_internship_listings")
        already_sent = {row[0] for row in cur.fetchall()}
        new_listings = [listing for listing in listings if listing["id"] not in already_sent]

        if not new_listings:
            print("--- All fetched listings were already sent. Skipping. ---")
            return

        for listing in new_listings:
            cur.execute(
                "INSERT INTO sent_internship_listings (listing_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (listing["id"],)
            )
        conn.commit()

        cur.execute("SELECT user_id FROM internship_subscribers WHERE status = 'subscribed'")
        subscriber_ids = [row[0] for row in cur.fetchall()]

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in internship_digest_job (database phase): {error}")
        return
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()

    if not new_listings or not subscriber_ids:
        print("--- No subscribers to notify. ---")
        return

    digest_lines = [
        f"• *{listing['title']}* at {listing['company']} ({listing['location']})\n  {listing['redirect_url']}"
        for listing in new_listings
    ]
    digest_text = "📋 *New marketing internships (posted 24-48 hours ago):*\n\n" + "\n\n".join(digest_lines)

    for user_id in subscriber_ids:
        try:
            dm = app.client.conversations_open(users=user_id)
            dm_channel_id = dm['channel']['id']
            app.client.chat_postMessage(channel=dm_channel_id, text=digest_text)
            print(f"--- Internship digest sent to {user_id} ---")
        except Exception as api_error:
            print(f"🔴 Error sending internship digest to {user_id}: {api_error}")

    print("--- Internship Digest Job Finished ---")
```

Note: `os` and `psycopg2` are already imported at the top of `jobs.py` — no new imports needed for those.

- [ ] **Step 3: Verify syntax**

```bash
source venv/bin/activate
python3 -c "import ast; ast.parse(open('jobs.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 4: Verify the job runs end-to-end against real data**

This will actually DM any real subscribers in the `internship_subscribers` table who are `subscribed` — since Task 3 was just built and no one has opted in yet via live Slack, this should be safe to run with zero subscribers. Confirm the subscriber table is empty first:

```bash
set -a; source .env; set +a
python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT COUNT(*) FROM internship_subscribers WHERE status = 'subscribed'\")
print('subscribed count:', cur.fetchone()[0])
"
```

If that prints `subscribed count: 0`, it's safe to run:

```bash
python3 -c "from jobs import internship_digest_job; internship_digest_job()"
```

Expected: prints `--- Running Internship Digest Job ---`, then either:
- `--- No new qualifying internship listings found. Skipping. ---` (if nothing in the 24-48h window right now), or
- fetches listings, inserts them into `sent_internship_listings`, then prints `--- No subscribers to notify. ---` (since subscriber count is 0)

Either way, no exception, and no `🔴 Error` lines.

If listings were found and recorded, verify they landed in the dedup table:

```bash
python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM sent_internship_listings')
print('sent_internship_listings count:', cur.fetchone()[0])
"
```

- [ ] **Step 5: Commit**

```bash
git add jobs.py
git commit -m "Add internship_digest_job to send daily internship DMs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Wire into `app.py`, deploy, and verify live in Slack

**Files:**
- Modify: `app.py:6-19`

**Interfaces:**
- Consumes: `handlers.internships` (Task 3, side-effecting import), `internship_digest_job` (Task 4).
- Produces: nothing consumed by later tasks — this is the final integration point.

- [ ] **Step 1: Update `app.py` imports and scheduler registration**

Change:

```python
from bot import app
from database import setup_database
from jobs import daily_bonus_job, birthday_job
import handlers.spot  # noqa: F401 — registers @app listeners
import handlers.assassin  # noqa: F401 — registers @app listeners
import handlers.birthday  # noqa: F401 — registers @app listeners
```

to:

```python
from bot import app
from database import setup_database
from jobs import daily_bonus_job, birthday_job, internship_digest_job
import handlers.spot  # noqa: F401 — registers @app listeners
import handlers.assassin  # noqa: F401 — registers @app listeners
import handlers.birthday  # noqa: F401 — registers @app listeners
import handlers.internships  # noqa: F401 — registers @app listeners
```

And change:

```python
    scheduler.add_job(birthday_job, 'cron', hour=9, minute=45)
```

to:

```python
    scheduler.add_job(birthday_job, 'cron', hour=9, minute=45)
    scheduler.add_job(internship_digest_job, 'cron', hour=12, minute=0)
```

- [ ] **Step 2: Verify syntax and that the app boots locally**

```bash
source venv/bin/activate
python3 -c "import ast; ast.parse(open('app.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

Then run the bot locally for a smoke test:

```bash
set -a; source .env; set +a
python3 -u app.py > /tmp/internship_smoke_test.log 2>&1 &
sleep 5
cat /tmp/internship_smoke_test.log
kill %1
```

Expected in the log: `✅ All database tables are ready.`, `⏰ Scheduler started. All jobs are scheduled.`, `⚡️ Spot Bot is running!`, `⚡️ Bolt app is running!` — no tracebacks.

- [ ] **Step 3: Add Adzuna secrets to Fly and deploy**

```bash
set -a; source .env; set +a
flyctl secrets set \
  ADZUNA_APP_ID="$ADZUNA_APP_ID" \
  ADZUNA_APP_KEY="$ADZUNA_APP_KEY" \
  -a umabot-uma-berkeley

flyctl deploy -a umabot-uma-berkeley
```

Expected: deploy completes, machine reaches "good state" (matches the pattern from every prior deploy in this project).

- [ ] **Step 4: Verify live in Slack**

As the admin user, in any channel the bot is in, send:

```
internship ask
```

Expected: the bot DMs you (and other real workspace members) asking to reply yes/no, then replies in-channel with `Internship ask complete. DMed N user(s) from the workspace who hadn't answered yet.`

Reply `yes` in the DM thread from the bot. Expected: bot replies `You're subscribed! I'll DM you when new marketing internships show up. 🎉`

Confirm the DB reflects it:

```bash
python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT user_id, status FROM internship_subscribers')
print(cur.fetchall())
"
```

Expected: your user ID shows `status = 'subscribed'`.

Then test unsubscribe — send `internship unsubscribe` in a DM to the bot. Expected: bot replies `You've been unsubscribed from internship alerts.` and the DB row's status flips to `declined`.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "Wire internship handlers and digest job into app startup

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
