import os
import re
import psycopg2

from bot import app, BOT_USER_ID
from config import ADMIN_USER_ID

BIRTHDAY_PATTERN = re.compile(r"^\s*(\d{1,2})[/\-](\d{1,2})\s*$")


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


def handle_birthday_setup_command(event, say, client, target_user_ids=None):
    if event['user'] != ADMIN_USER_ID:
        say("Sorry, only the designated admin can run birthday setup.")
        return

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM birthdays")
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
                    text="🎂 Reply with your birthday as MM/DD (e.g. 03/14) and we'll get to celebrate it together!"
                )
                dm_count += 1
            except Exception as dm_error:
                print(f"🔴 Error DMing {user_id} for birthday setup: {dm_error}")

        scope = f"{len(candidate_ids)} selected user(s)" if target_user_ids else "the workspace"
        say(f"Birthday setup complete. DMed {dm_count} user(s) from {scope} who don't have a birthday on file yet.")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in handle_birthday_setup_command: {error}")
        say("Sorry, something went wrong running birthday setup.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.message(re.compile(r"^birthday setup", re.IGNORECASE))
def handle_birthday_setup_message(message, say, client):
    target_user_ids = [
        user_id for user_id in re.findall(r"<@(\w+)>", message['text'])
        if user_id != BOT_USER_ID
    ]
    handle_birthday_setup_command(message, say, client, target_user_ids=target_user_ids or None)


def is_birthday_dm(message):
    return message.get('channel_type') == 'im' and 'text' in message and 'bot_id' not in message


@app.message(matchers=[is_birthday_dm])
def handle_birthday_dm(message, say):
    match = BIRTHDAY_PATTERN.match(message['text'])
    if not match:
        return

    month, day = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        say("That doesn't look like a valid date. Please send your birthday as MM/DD (e.g. 03/14).")
        return

    user_id = message['user']
    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO birthdays (user_id, birth_month, birth_day)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET birth_month = EXCLUDED.birth_month, birth_day = EXCLUDED.birth_day
            """,
            (user_id, month, day)
        )
        conn.commit()
        say(f"Got it! I've saved your birthday as {month:02d}/{day:02d}. 🎉")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error saving birthday for {user_id}: {error}")
        say("Sorry, something went wrong saving your birthday. Please try again.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
