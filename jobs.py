import os
import random
import psycopg2

from bot import app
from config import daily_bonus_users
from utils import get_user_name


def daily_bonus_job():
    print("--- Running Daily Bonus Job ---")
    daily_bonus_users.clear()

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT DISTINCT channel_id FROM spots")
        active_channels = [row[0] for row in cur.fetchall()]
        print(f"--- Found active channels for bonus job: {active_channels} ---")

        new_bonus_assignments = {}

        for channel_id in active_channels:
            cur.execute("""
                SELECT DISTINCT user_id FROM (
                    SELECT spotter_id as user_id FROM spots WHERE channel_id = %s
                    UNION
                    SELECT spotted_id as user_id FROM spots WHERE channel_id = %s
                ) as participants
            """, (channel_id, channel_id))

            participants = [row[0] for row in cur.fetchall()]
            print(f"--- Found participants for channel {channel_id}: {participants} ---")

            if len(participants) >= 2:
                bonus_targets = random.sample(participants, 2)
                new_bonus_assignments[channel_id] = set(bonus_targets)

                user1_name = get_user_name(bonus_targets[0])
                user2_name = get_user_name(bonus_targets[1])
                announcement = f"🎉 *Daily Bonus!* 🎉\nToday's bonus targets are *{user1_name}* and *{user2_name}*! Spots of them are worth 2 points!"
                try:
                    app.client.chat_postMessage(channel=channel_id, text=announcement)
                    print(f"--- Bonus users announced for channel {channel_id}: {bonus_targets} ---")
                except Exception as api_error:
                    print(f"🔴 Error posting bonus announcement to {channel_id}: {api_error}")
            else:
                print(f"--- Not enough participants in channel {channel_id} to assign bonus targets. ---")

        daily_bonus_users.update(new_bonus_assignments)
        print("--- Daily Bonus Job Finished ---")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in daily_bonus_job: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
