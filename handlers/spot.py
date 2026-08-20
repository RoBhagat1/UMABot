import os
import re
import io
import random
import psycopg2
import requests
from PIL import Image

from bot import app, BOT_USER_ID
from config import ADMIN_USER_ID, EXPLOSIONS_DIR, daily_bonus_users
from utils import get_user_name, get_current_season_id, announce_season_winner
from jobs import daily_bonus_job


# --- Spot Detection ---

def is_spot_message_and_not_command(message):
    text = message.get("text", "")
    has_keyword = re.search(r"\b(spot|spotted)\b", text, re.IGNORECASE)
    is_command = text.strip().startswith(f"<@{BOT_USER_ID}>")
    return has_keyword and not is_command


@app.message(matchers=[is_spot_message_and_not_command])
def handle_spot_message(message, say):
    print("\n--- DEBUG: `handle_spot_message` was triggered. ---")

    if 'user' not in message or 'files' not in message or 'text' not in message:
        return

    spotter_id = message['user']
    text = message['text']
    channel_id = message['channel']

    mentioned_users = set(re.findall(r"<@(\w+)>", text))
    if not mentioned_users:
        return

    successful_spots = 0
    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        season_id = get_current_season_id(channel_id)

        for spotted_id in mentioned_users:
            if spotter_id == spotted_id:
                continue

            spotter_points_to_award = 1
            if channel_id in daily_bonus_users and spotted_id in daily_bonus_users.get(channel_id, set()):
                spotter_points_to_award = 2
                print(f"--- DEBUG: Awarding 2 bonus points for spotting {spotted_id} in {channel_id}. ---")

            insert_command = """
            INSERT INTO spots (spotter_id, spotted_id, channel_id, message_ts, image_url, season_id, spotter_points, caught_points)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_ts, spotted_id) DO NOTHING;
            """
            spot_data = (
                spotter_id,
                spotted_id,
                channel_id,
                message['ts'],
                message['files'][0]['url_private'],
                season_id,
                spotter_points_to_award,
                1,
            )
            cur.execute(insert_command, spot_data)
            if cur.rowcount > 0:
                successful_spots += 1


        conn.commit()

        if successful_spots > 0:
            app.client.reactions_add(
                channel=message['channel'],
                timestamp=message['ts'],
                name="white_check_mark"
            )

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 DEBUG: An error occurred during database operation: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.event({"type": "message", "subtype": "message_deleted"})
def handle_message_deletion(event):
    print("\n--- DEBUG: `handle_message_deletion` (subtype) was triggered. ---")

    if 'previous_message' not in event or 'ts' not in event['previous_message']:
        print("--- DEBUG: No previous_message or ts found in deletion event. Skipping. ---")
        return

    deleted_ts = event['previous_message']['ts']
    print(f"--- DEBUG: A message with timestamp {deleted_ts} was deleted. Checking database. ---")

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("DELETE FROM spots WHERE message_ts = %s", (deleted_ts,))

        if cur.rowcount > 0:
            print(f"--- SUCCESS: Deleted {cur.rowcount} spot record(s) with timestamp {deleted_ts}. ---")
        else:
            print(f"--- INFO: Deleted message {deleted_ts} was not a spot record. No action taken. ---")

        conn.commit()

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 DEBUG: An error occurred during message deletion handling: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


# --- Command Handlers ---

def handle_spotboard_command(message, say):
    try:
        channel_id = message['channel']
        current_season = get_current_season_id(channel_id)

        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        query = """
            SELECT spotter_id, SUM(spotter_points) AS total_score
            FROM spots
            WHERE season_id = %s AND channel_id = %s
        """
        params = [current_season, channel_id]

        query += """
            GROUP BY spotter_id
            ORDER BY total_score DESC, spotter_id ASC
            LIMIT 5;
        """

        cur.execute(query, tuple(params))
        results = cur.fetchall()
        cur.close()
        conn.close()

        if not results:
            say("No spots have been recorded this season since the last reset!")
            return

        leaderboard_text = "*Spotboard:*\n\n"
        for i, row in enumerate(results):
            user_id, score = row
            score = int(score)
            user_name = get_user_name(user_id)
            leaderboard_text += f"{i+1}. {user_name} - {score}\n"

        say(leaderboard_text)

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling spotboard command: {error}")
        say("Sorry, I had trouble fetching the spotboard.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_caughtboard_command(message, say):
    try:
        channel_id = message['channel']
        current_season = get_current_season_id(channel_id)

        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        query = """
            SELECT spotted_id, SUM(caught_points) AS total_score
            FROM spots
            WHERE season_id = %s AND channel_id = %s
        """
        params = [current_season, channel_id]

        query += """
            GROUP BY spotted_id
            ORDER BY total_score DESC, spotted_id ASC
            LIMIT 5;
        """

        cur.execute(query, tuple(params))
        results = cur.fetchall()
        cur.close()
        conn.close()

        if not results:
            say("No one has been spotted this season since the last reset!")
            return

        leaderboard_text = "*Caughtboard:*\n\n"
        for i, row in enumerate(results):
            user_id, score = row
            score = int(score)
            user_name = get_user_name(user_id)
            leaderboard_text += f"{i+1}. {user_name} - {score}\n"

        say(leaderboard_text)

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling caughtboard command: {error}")
        say("Sorry, I had trouble fetching the caughtboard.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_alltime_spotboard_command(message, say):
    try:
        channel_id = message['channel']
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        query = """
            SELECT spotter_id, SUM(spotter_points) AS total_score
            FROM spots
            WHERE channel_id = %s
            GROUP BY spotter_id
            ORDER BY total_score DESC, spotter_id ASC
            LIMIT 5;
        """
        cur.execute(query, (channel_id,))
        results = cur.fetchall()
        cur.close()
        conn.close()
        if not results:
            say("No spots have ever been recorded in this channel!")
            return
        leaderboard_text = "*All-time Spotboard:*\n\n"
        for i, row in enumerate(results):
            user_id, score = row
            score = int(score)
            user_name = get_user_name(user_id)
            leaderboard_text += f"{i+1}. {user_name} - {score}\n"
        say(leaderboard_text)
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling all-time spotboard command: {error}")
        say("Sorry, I had trouble fetching the all-time spotboard.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_alltime_caughtboard_command(message, say):
    try:
        channel_id = message['channel']
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        query = """
            SELECT spotted_id, SUM(caught_points) AS total_score
            FROM spots
            WHERE channel_id = %s
            GROUP BY spotted_id
            ORDER BY total_score DESC, spotted_id ASC
            LIMIT 5;
        """
        cur.execute(query, (channel_id,))
        results = cur.fetchall()
        cur.close()
        conn.close()
        if not results:
            say("No one has ever been caught in this channel!")
            return
        leaderboard_text = "*All-time Caughtboard:*\n\n"
        for i, row in enumerate(results):
            user_id, score = row
            score = int(score)
            user_name = get_user_name(user_id)
            leaderboard_text += f"{i+1}. {user_name} - {score}\n"
        say(leaderboard_text)
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling all-time caughtboard command: {error}")
        say("Sorry, I had trouble fetching the all-time caughtboard.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_miss_you_command(message, say):
    try:
        text = message.get('text', '')
        mentioned_users = re.findall(r"<@(\w+)>", text)

        if not mentioned_users:
            say("You need to tell me who you miss! Please mention a user, like `miss you @Rohan`.")
            return

        target_user_id = mentioned_users[0]
        channel_id = message['channel']

        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT image_url FROM spots WHERE spotted_id = %s AND channel_id = %s AND is_valid = TRUE", (target_user_id, channel_id))
        image_urls = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        if not image_urls:
            target_user_name = get_user_name(target_user_id)
            say(f"Sorry, I couldn't find any pictures of {target_user_name} in this channel.")
            return

        random_image_url = random.choice(image_urls)
        target_user_name = get_user_name(target_user_id)
        say(f"Missing them? Here's a memory of {target_user_name}!\n{random_image_url}")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling 'miss you' command: {error}")
        say("Sorry, I had a problem fetching that picture.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_mystats_command(message, say):
    try:
        user_id = message['user']
        channel_id = message['channel']

        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT SUM(spotter_points) FROM spots WHERE spotter_id = %s AND channel_id = %s AND is_valid = TRUE", (user_id, channel_id))
        spots_made = cur.fetchone()[0] or 0

        cur.execute("SELECT SUM(caught_points) FROM spots WHERE spotted_id = %s AND channel_id = %s AND is_valid = TRUE", (user_id, channel_id))
        times_caught = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT spotted_id, COUNT(*) as spot_count
            FROM spots
            WHERE spotter_id = %s AND channel_id = %s            GROUP BY spotted_id
            ORDER BY spot_count DESC
            LIMIT 1;
        """, (user_id, channel_id))
        nemesis_result = cur.fetchone()

        cur.close()
        conn.close()

        user_name = get_user_name(user_id)
        stats_text = f"📊 *{user_name}'s Spotting Record in this channel:*\n\n"
        stats_text += f"• You have spotted others *{int(spots_made)}* times.\n"
        stats_text += f"• You have been spotted *{int(times_caught)}* times.\n"

        if nemesis_result:
            nemesis_id, nemesis_count = nemesis_result
            nemesis_name = get_user_name(nemesis_id)
            stats_text += f"• Your most frequent target is *{nemesis_name}* ({nemesis_count} spots)."
        else:
            stats_text += "• You haven't spotted anyone yet!"

        say(stats_text)

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error handling mystats command: {error}")
        say("Sorry, I had trouble fetching your stats.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_explode_command(message, say, client):
    try:
        text = message.get('text', '')
        mentioned_users = re.findall(r"<@(\w+)>", text)

        if not mentioned_users:
            say("You need to tell me who to explode! Please mention a user, like `explode @Rohan`.")
            return

        target_user_id = mentioned_users[0]
        channel_id = message['channel']

        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT image_url FROM spots WHERE spotted_id = %s AND channel_id = %s AND is_valid = TRUE", (target_user_id, channel_id))
        image_urls = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()

        if not image_urls:
            target_user_name = get_user_name(target_user_id)
            say(f"Sorry, I couldn't find any pictures of {target_user_name} to explode.")
            return

        random_image_url = random.choice(image_urls)

        auth_header = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        user_image_response = requests.get(random_image_url, headers=auth_header)
        user_image_response.raise_for_status()

        try:
            explosion_files = [f for f in os.listdir(EXPLOSIONS_DIR) if f.lower().endswith('.png')]
            if not explosion_files:
                say("I couldn't find any explosion images in my folder!")
                return
            random_explosion_path = os.path.join(EXPLOSIONS_DIR, random.choice(explosion_files))
        except FileNotFoundError:
            print(f"🔴 Error: The directory '{EXPLOSIONS_DIR}' was not found.")
            say("I'm having trouble finding my explosion effects. Please check my configuration.")
            return

        base_image = Image.open(io.BytesIO(user_image_response.content)).convert("RGBA")
        explosion_image = Image.open(random_explosion_path).convert("RGBA")
        explosion_image = explosion_image.resize(base_image.size)
        composite_image = Image.alpha_composite(base_image, explosion_image)

        temp_file = io.BytesIO()
        composite_image.save(temp_file, format='PNG')
        temp_file.seek(0)

        target_user_name = get_user_name(target_user_id)
        client.files_upload_v2(
            channel=channel_id,
            initial_comment=f"💥 {target_user_name} has been exploded! 💥",
            file=temp_file,
            filename="explosion.png"
        )

    except Exception as e:
        print(f"🔴 Error in explode command: {e}")
        say("Sorry, I had trouble creating the explosion. The image might be too powerful.")
    finally:
        if 'cur' in locals() and cur is not None and not cur.closed:
            cur.close()
        if 'conn' in locals() and conn is not None and not conn.closed:
            conn.close()


def handle_daily_bonus_command(message, say):
    channel_id = message['channel']
    if channel_id in daily_bonus_users and daily_bonus_users[channel_id]:
        targets = list(daily_bonus_users[channel_id])
        user1_name = get_user_name(targets[0])
        user2_name = get_user_name(targets[1])
        say(f"Today's bonus targets are *{user1_name}* and *{user2_name}*! Spots of them are worth 2 points.")
    else:
        say("Bonus targets haven't been assigned for today yet, or this channel isn't active in the Spot Bot game.")


def handle_spot_help_command(message, say):
    help_text = """
*Spot Bot Commands:*
• `spot @user` or `spotted @user` (with image): Record a spot. Counts for 1 point.
• `spotboard`: Show the seasonal leaderboard of top spotters.
• `caughtboard`: Show the seasonal leaderboard of most spotted players.
• `alltimespotboard`: Show the all-time leaderboard of top spotters.
• `alltimecaughtboard`: Show the all-time leaderboard of most spotted players.
• `reset`: Manually end the current season and start a new one (admin only).
• `birthday setup`: DMs everyone without a birthday on file to ask for it (admin only). Add `@user`s to only DM specific people, e.g. `birthday setup @alice @bob`. Reply to the DM with your birthday as MM/DD.
• `miss you @user` or `i miss u @user`: Shows a random past spot picture of the mentioned user.
• `mystats`: Shows your personal spotting stats in this channel.
• `explode @user`: Overlays a random explosion on a random spot picture of the mentioned user.
• `help`: Shows this help message.
• `assassin help`: Show commands for the Assassin game.
"""
    say(help_text)


# --- Keyword Listeners ---

@app.message(re.compile(r"^spotboard$", re.IGNORECASE))
def handle_spotboard_keyword(message, say):
    handle_spotboard_command(message, say)


@app.message(re.compile(r"^caughtboard$", re.IGNORECASE))
def handle_caughtboard_keyword(message, say):
    handle_caughtboard_command(message, say)


@app.message(re.compile(r"^(alltimespotboard|all time spot board)$", re.IGNORECASE))
def handle_alltime_spotboard_keyword(message, say):
    handle_alltime_spotboard_command(message, say)


@app.message(re.compile(r"^(alltimecaughtboard|all time caught board)$", re.IGNORECASE))
def handle_alltime_caughtboard_keyword(message, say):
    handle_alltime_caughtboard_command(message, say)


@app.message(re.compile(r"^reset$", re.IGNORECASE))
def handle_reset_request(message, client):
    if message['user'] != ADMIN_USER_ID:
        client.chat_postEphemeral(
            channel=message['channel'],
            user=message['user'],
            text="Sorry, only the designated admin can reset the Spot Bot season."
        )
        return
    try:
        client.chat_postEphemeral(
            channel=message['channel'],
            user=message['user'],
            text="Are you sure you want to reset the seasonal leaderboards? This cannot be undone.",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "Are you sure you want to reset the seasonal leaderboards? This will announce the winner of the current interim season and start a fresh board."}},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Confirm Reset"}, "style": "danger", "action_id": "confirm_reset_action"},
                    {"type": "button", "text": {"type": "plain_text", "text": "Cancel"}, "action_id": "cancel_reset_action"}
                ]}
            ]
        )
    except Exception as e:
        print(f"🔴 Error sending reset confirmation: {e}")


@app.message(re.compile(r"^(i miss (you|u)|miss (you|u))", re.IGNORECASE))
def handle_miss_you_keyword(message, say):
    handle_miss_you_command(message, say)


@app.message(re.compile(r"^mystats$", re.IGNORECASE))
def handle_mystats_keyword(message, say):
    handle_mystats_command(message, say)


@app.message(re.compile(r"^explode", re.IGNORECASE))
def handle_explode_keyword(message, say, client):
    handle_explode_command(message, say, client)


@app.message(re.compile(r"^help$", re.IGNORECASE))
def handle_spot_help_keyword(message, say):
    handle_spot_help_command(message, say)


@app.message(re.compile(r"^dailybonus$", re.IGNORECASE))
def handle_daily_bonus_keyword(message, say):
    handle_daily_bonus_command(message, say)


# --- Action Listeners ---

@app.action("confirm_reset_action")
def handle_confirm_reset_action(ack, body, client):
    ack()
    conn = None
    cur = None
    try:
        channel_id = body['channel']['id']
        season_to_end_id = get_current_season_id(channel_id)
        announce_season_winner(season_to_end_id, channel_id, is_manual_reset=True)

        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("INSERT INTO channel_seasons (channel_id) VALUES (%s)", (channel_id,))
        conn.commit()
        print(f"--- MANUAL RESET: New season started for channel {channel_id} ---")

        client.chat_delete(
            channel=body['channel']['id'],
            ts=body['message']['ts']
        )
    except Exception as e:
        print(f"🔴 Error in confirm_reset_action: {e}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


@app.action("cancel_reset_action")
def handle_cancel_reset_action(ack, body, client):
    ack()
    try:
        client.chat_delete(
            channel=body['channel']['id'],
            ts=body['message']['ts']
        )
    except Exception as e:
        print(f"🔴 Error in cancel_reset_action: {e}")


# --- App Mention Handler ---

@app.event("app_mention")
def handle_mention(event, say, client):
    from handlers.assassin import (
        handle_assassin_start_command, handle_assassin_target_command,
        handle_eliminated_command, handle_assassin_alive_command,
        handle_assassin_dead_command, handle_assassin_killcount_command,
        handle_assassin_end_request, handle_assassin_targets_command,
        handle_assassin_remove_command, handle_assassin_help_command,
    )
    command_text = event['text'].strip().lower()
    command_part = re.sub(r'^<@\w+>\s*', '', command_text).strip()

    if command_part.startswith("assassin start"):
        handle_assassin_start_command(event, say, client)
    elif command_part == "assassin target" or command_part == "mytarget":
        handle_assassin_target_command(event, say, client)
    elif command_part.startswith("eliminated") or command_part.startswith("eliminate"):
        handle_eliminated_command(event, say, client)
    elif command_part == "assassin alive":
        handle_assassin_alive_command(event, say)
    elif command_part == "assassin dead":
        handle_assassin_dead_command(event, say)
    elif command_part == "assassin killcount":
        handle_assassin_killcount_command(event, say)
    elif command_part == "assassin end":
        handle_assassin_end_request(event, client, say)
    elif command_part == "assassin targets":
        handle_assassin_targets_command(event, client)
    elif command_part.startswith("assassin remove"):
        handle_assassin_remove_command(event, say, client)
    elif command_part == "assassin help":
        handle_assassin_help_command(event, say)
    elif command_part.startswith("explode"):
        handle_explode_command(event, say, client)
    elif command_part == "mystats":
        handle_mystats_command(event, say)
    elif command_part.startswith("miss"):
        handle_miss_you_command(event, say)
    elif command_part == "alltimecaughtboard" or command_part == "all time caught board":
        handle_alltime_caughtboard_command(event, say)
    elif command_part == "alltimespotboard" or command_part == "all time spot board":
        handle_alltime_spotboard_command(event, say)
    elif command_part == "caughtboard":
        handle_caughtboard_command(event, say)
    elif command_part == "spotboard":
        handle_spotboard_command(event, say)
    elif command_part == "test bonus":
        say("Sure, I'll run the daily bonus job for you right now. Check the channel for an announcement if it's eligible.")
        daily_bonus_job()
    elif command_part == "dailybonus":
        handle_daily_bonus_command(event, say)
    else:
        handle_spot_help_command(event, say)
