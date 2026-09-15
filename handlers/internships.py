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
