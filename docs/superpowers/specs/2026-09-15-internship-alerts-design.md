# Marketing Internship Alerts — Design

## Purpose

Give UMABot the ability to notify opted-in members, once a day, about
new marketing internship postings that appeared 24–48 hours ago. This
mirrors the existing birthday feature's opt-in/broadcast pattern but
sources its data from an external job API (Adzuna) instead of
user-submitted info.

## Data source

**Adzuna API** (developer.adzuna.com), free tier. Chosen over:
- LinkedIn Jobs API — requires a partnership, not accessible for this project.
- Indeed — public third-party API access has been discontinued; scraping violates ToS.
- Jobright.ai — investigated directly; it's an authenticated, personalized
  dashboard with no public API or raw listing data, and no clean posted-date
  field to filter on.
- GitHub-style internship-tracker repos — free but skew toward SWE roles,
  thin marketing coverage, dependent on volunteer maintenance.
- Scraping individual company career pages — brittle, no reliable posted-date field.

Adzuna gives structured JSON search results with a real `created` timestamp
per listing, which is the load-bearing requirement (filtering to a specific
24–48h-old window). Search scope: United States, nationwide.

Credentials: `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`, obtained via free signup
at developer.adzuna.com, stored in `.env` locally and as Fly secrets in
production — never committed.

## Data model

Two new tables, added to `database.py`'s `setup_database()`:

```sql
CREATE TABLE IF NOT EXISTS internship_subscribers (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('subscribed', 'declined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sent_internship_listings (
    listing_id TEXT PRIMARY KEY,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

A row is only written to `internship_subscribers` once a user actually
answers yes or no to the ask DM — `status='subscribed'` receives daily
digests, `status='declined'` receives nothing. A user who was DMed but
never replied has no row at all.

`internship ask` re-runs (broadcast or targeted at specific `@user`s)
skip anyone who already has a row, mirroring `birthday setup`'s
`known_user_ids` check. Concretely: someone who explicitly answered
(subscribed or declined) will never be re-asked by a later run, but
someone who was asked and simply never replied has no row yet and *will*
be re-DMed by a later broadcast — same as birthday setup's behavior
toward silent non-responders. This is intentional: it's fine to nudge
someone who never saw/answered the prompt, but an explicit "no" is
respected permanently within this iteration (see "Explicitly out of
scope" for revisiting a declined choice).

`sent_internship_listings` is a global, cross-user dedup table: once a
given Adzuna listing has been sent to subscribers, its `listing_id` is
recorded so it's never sent again on a later run, even if it technically
still falls within some future day's 24–48h window (it won't, given the
run cadence, but this also guards against manual re-runs during testing).

## Opt-in / unsubscribe flow

New module `handlers/internships.py`, following the same shape as
`handlers/birthday.py`:

- **`internship ask`** — plain-text command (`@app.message`), admin-only
  (checked against `ADMIN_USER_ID`, same as `birthday setup`). DMs every
  workspace user who does not already have a row in
  `internship_subscribers`, asking whether they want daily marketing
  internship alerts, and to reply yes/no. Supports the same optional
  `@user` targeting birthday setup has (DM only specific people instead of
  the whole workspace).
- **DM reply listener** — parses `yes`/`y`/`no`/`n` (case-insensitive) from
  a direct message to the bot and upserts the sender's row in
  `internship_subscribers` with the corresponding status. Anything else is
  ignored (falls through, same as the birthday DM listener ignoring
  non-date replies).
- **`internship unsubscribe`** — plain-text command, any user, any channel
  or DM. Sets (or inserts) the sender's own row to `status='declined'`.

## Daily digest job

New function `internship_digest_job()` in `jobs.py`, scheduled in
`app.py` via APScheduler at **12:00 PM Pacific** (`hour=12, minute=0`),
alongside the existing `birthday_job` registration.

Steps:
1. Call Adzuna's search endpoint:
   `GET https://api.adzuna.com/v1/api/jobs/us/search/1`
   with `app_id`, `app_key`, `what=marketing internship`, `sort_by=date`,
   `max_days_old=2`, a reasonable `results_per_page` (e.g. 50).
2. For each result, parse its `created` timestamp and keep only those
   whose age is between 24 and 48 hours at job-run time, and whose
   `id` is not already present in `sent_internship_listings`.
3. If the filtered set is empty, log and exit — **no DMs are sent** that
   day (per decision: silence over a low-value "nothing new" ping).
4. Otherwise, insert the new listing IDs into `sent_internship_listings`,
   then query all `user_id` with `status='subscribed'` from
   `internship_subscribers`.
5. DM each subscriber a formatted digest (title, company, location, apply
   link) via `chat_postMessage` to a DM channel (`conversations_open`,
   same pattern as birthday setup DMs). Each send is wrapped in its own
   try/except, matching `birthday_job`'s per-user error isolation — one
   failed DM (e.g. user left the workspace) must not abort the rest of
   the loop.

## Error handling

- Adzuna request failure (network error, non-200, malformed JSON): caught,
  logged, job exits for the day — no partial/garbled digest sent. Retried
  naturally on the next day's scheduled run.
- Per-subscriber DM failure: caught and logged individually; does not
  affect other subscribers or mark the listings as unsent (they've already
  been recorded in `sent_internship_listings`, so a DM failure does not
  cause a duplicate resend attempt next run — this is an accepted
  trade-off for simplicity, consistent with how `birthday_job` handles
  per-user post failures).

## Config

- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` — new required env vars, added to
  `.env` and Fly secrets.
- Search query string (`"marketing internship"`) and country scope (`us`)
  live as constants in `handlers/internships.py` or `config.py` — not
  user-configurable via Slack in this iteration (YAGNI; can be revisited
  if the org wants to broaden/narrow the search later).

## Testing / rollout

Before relying on the noon cron:
- Verify `internship ask` DMs correctly and that yes/no replies persist
  to `internship_subscribers` as expected (mirrors how birthday setup was
  manually tested).
- Manually invoke `internship_digest_job()` from a local script against
  the real DB/Slack (same technique used to test `birthday_job` without
  waiting for its scheduled time), to confirm the Adzuna call, filtering,
  dedup, and DM formatting all work end to end before the first scheduled
  run.
- Confirm the bot's Fly deployment has the two new env vars set before
  the job's first live run, or it will fail cleanly (logged error, no
  crash, no DMs) rather than send anything malformed.

## Explicitly out of scope (YAGNI)

- Per-user customizable search terms/location.
- Any UI/command to browse past digests.
- A way for a user who declined to change their mind and re-subscribe
  themselves (only self-serve `internship unsubscribe` exists in this
  iteration, no self-serve subscribe). An admin re-running `internship
  ask` will not reach them either, since they already have a row (see
  "Data model"). Revisiting a declined choice would require a direct DB
  update or a future `internship subscribe` command.
