import os
import re
import psycopg2

from bot import app, BOT_USER_ID
from config import ADMIN_USER_ID
from job_boards import CATEGORIES, CATEGORY_KEYS

YES_PATTERN = re.compile(r"^\s*y(es)?\s*$", re.IGNORECASE)
NO_PATTERN = re.compile(r"^\s*n(o)?\s*$", re.IGNORECASE)

CATEGORY_LABELS = {c["key"]: c["label"] for c in CATEGORIES}
CATEGORY_ASK_TEXT = {c["key"]: c["ask_text"] for c in CATEGORIES}


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


def _db_connect():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


def _answered_categories_by_user(cur, user_ids):
    if not user_ids:
        return {}
    cur.execute(
        "SELECT user_id, category FROM internship_subscriptions WHERE user_id = ANY(%s)",
        (list(user_ids),)
    )
    answered = {}
    for user_id, category in cur.fetchall():
        answered.setdefault(user_id, set()).add(category)
    return answered


def _pending_users(cur, user_ids):
    if not user_ids:
        return set()
    cur.execute(
        "SELECT user_id FROM internship_ask_pending WHERE user_id = ANY(%s)",
        (list(user_ids),)
    )
    return {row[0] for row in cur.fetchall()}


def _next_missing_category(answered_keys):
    for key in CATEGORY_KEYS:
        if key not in answered_keys:
            return key
    return None


def _set_pending(cur, user_id, category):
    cur.execute(
        """
        INSERT INTO internship_ask_pending (user_id, pending_category)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET pending_category = EXCLUDED.pending_category
        """,
        (user_id, category)
    )


def _clear_pending(cur, user_id):
    cur.execute("DELETE FROM internship_ask_pending WHERE user_id = %s", (user_id,))


def _get_pending_category(user_id):
    conn = None
    cur = None
    try:
        conn = _db_connect()
        cur = conn.cursor()
        cur.execute("SELECT pending_category FROM internship_ask_pending WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_internship_ask_command(event, say, client, target_user_ids=None):
    if event['user'] != ADMIN_USER_ID:
        say("Sorry, only the designated admin can run internship ask.")
        return

    conn = None
    cur = None
    try:
        conn = _db_connect()
        cur = conn.cursor()

        if target_user_ids:
            candidate_ids = target_user_ids
        else:
            candidate_ids = _all_workspace_user_ids(client)

        answered_by_user = _answered_categories_by_user(cur, candidate_ids)
        already_pending = _pending_users(cur, candidate_ids)

        dm_count = 0
        for user_id in candidate_ids:
            if user_id in already_pending:
                continue
            missing = _next_missing_category(answered_by_user.get(user_id, set()))
            if missing is None:
                continue
            try:
                dm = client.conversations_open(users=user_id)
                dm_channel_id = dm['channel']['id']
                client.chat_postMessage(
                    channel=dm_channel_id,
                    text=f"📋 {CATEGORY_ASK_TEXT[missing]} Reply here with yes or no."
                )
                _set_pending(cur, user_id, missing)
                dm_count += 1
            except Exception as dm_error:
                print(f"🔴 Error DMing {user_id} for internship ask: {dm_error}")

        conn.commit()
        scope = f"{len(candidate_ids)} selected user(s)" if target_user_ids else "the workspace"
        say(f"Internship ask complete. DMed {dm_count} user(s) from {scope} who had a missing answer.")

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


def is_internship_reply(message):
    if message.get('channel_type') != 'im' or 'text' not in message or 'bot_id' in message:
        return False
    text = message['text']
    if not (YES_PATTERN.match(text) or NO_PATTERN.match(text)):
        return False
    return _get_pending_category(message['user']) is not None


@app.message(matchers=[is_internship_reply])
def handle_internship_reply(message, say):
    user_id = message['user']
    text = message['text']
    status = "subscribed" if YES_PATTERN.match(text) else "declined"

    conn = None
    cur = None
    try:
        conn = _db_connect()
        cur = conn.cursor()
        cur.execute("SELECT pending_category FROM internship_ask_pending WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            return
        category = row[0]

        cur.execute(
            """
            INSERT INTO internship_subscriptions (user_id, category, status)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, category) DO UPDATE SET status = EXCLUDED.status
            """,
            (user_id, category, status)
        )

        label = CATEGORY_LABELS.get(category, category)
        if status == "subscribed":
            say(f"You're subscribed to {label} alerts! 🎉")
        else:
            say(f"Got it, no {label} alerts.")

        answered = _answered_categories_by_user(cur, [user_id]).get(user_id, set())
        answered.add(category)
        next_category = _next_missing_category(answered)

        if next_category is not None:
            _set_pending(cur, user_id, next_category)
            conn.commit()
            say(f"📋 {CATEGORY_ASK_TEXT[next_category]} Reply here with yes or no.")
        else:
            _clear_pending(cur, user_id)
            conn.commit()

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error saving internship subscription for {user_id}: {error}")
        say("Sorry, something went wrong saving your answer. Please try again.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.message(re.compile(r"^internship unsubscribe\s*$", re.IGNORECASE))
def handle_internship_unsubscribe(message, say):
    user_id = message['user']
    conn = None
    cur = None
    try:
        conn = _db_connect()
        cur = conn.cursor()
        for category in CATEGORY_KEYS:
            cur.execute(
                """
                INSERT INTO internship_subscriptions (user_id, category, status)
                VALUES (%s, %s, 'declined')
                ON CONFLICT (user_id, category) DO UPDATE SET status = 'declined'
                """,
                (user_id, category)
            )
        _clear_pending(cur, user_id)
        conn.commit()
        say("You've been unsubscribed from all internship alerts.")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error unsubscribing {user_id} from internships: {error}")
        say("Sorry, something went wrong unsubscribing you. Please try again.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
