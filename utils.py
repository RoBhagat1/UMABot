import os
import psycopg2
import pytz
from datetime import datetime, timedelta

from bot import app
from config import SEASON_START_DATE, user_cache


def get_current_season_id():
    now = datetime.now(pytz.timezone('America/Los_Angeles'))
    delta_days = (now - SEASON_START_DATE).days
    seasons_passed = delta_days // 14
    current_season_start = SEASON_START_DATE + timedelta(days=(seasons_passed * 14))
    return current_season_start.strftime('%Y-%m-%d')


def get_user_name(user_id):
    if user_id in user_cache:
        return user_cache[user_id]
    try:
        result = app.client.users_info(user=user_id)
        user_name = result['user']['profile'].get(
            'real_name',
            result['user']['profile'].get('display_name', result['user']['name'])
        )
        user_cache[user_id] = user_name
        return user_name
    except Exception as e:
        print(f"Error fetching user info for {user_id}: {e}")
        return f"User ({user_id})"


def announce_season_winner(season_id_to_process, channel_id, is_manual_reset=False):
    print(f"--- Announcing winner for season: {season_id_to_process} in channel {channel_id} ---")
    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        winner_query = """
            SELECT spotter_id, SUM(spotter_points) AS total_score
            FROM spots
            WHERE season_id = %s AND channel_id = %s AND is_valid = TRUE
            GROUP BY spotter_id
            ORDER BY total_score DESC
            LIMIT 1;
        """
        cur.execute(winner_query, (season_id_to_process, channel_id))
        winner_result = cur.fetchone()

        if is_manual_reset:
            announcement = "✅ *Manual Reset Complete!*\n\n"
        else:
            announcement = "🏆 A new Spotting Season has begun! 📸\n\n"

        if winner_result:
            winner_id, winner_score = winner_result
            winner_name = get_user_name(winner_id)
            period = "interim season" if is_manual_reset else "last season"
            announcement += f"Congratulations to *{winner_name}* for winning the {period} with {int(winner_score)} spots!"
        else:
            announcement += "No spots were recorded in the last period. A fresh start!"

        app.client.chat_postMessage(channel=channel_id, text=announcement)

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in announce_season_winner: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
