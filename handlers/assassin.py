import os
import re
import random
import psycopg2

from bot import app
from config import ADMIN_USER_ID
from utils import get_user_name


def handle_assassin_start_command(message, say, client):
    channel_id = message['channel']
    starter_id = message['user']
    text = message.get('text', '')

    if starter_id != ADMIN_USER_ID:
        client.chat_postEphemeral(
            channel=channel_id,
            user=starter_id,
            text="Sorry, only the designated admin can start an Assassin game."
        )
        return

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM assassin_players WHERE channel_id = %s AND is_active = TRUE", (channel_id,))
        active_game_count = cur.fetchone()[0]
        if active_game_count > 0:
            say("An Assassin game is already in progress in this channel! Use `assassin end` to stop it first.")
            return

        mentioned_users = list(set(re.findall(r"<@(\w+)>", text)))
        if len(mentioned_users) < 3:
            say("You need at least 3 players to start a game of Assassin. Please mention everyone who is playing.")
            return

        cur.execute("DELETE FROM assassin_players WHERE channel_id = %s", (channel_id,))
        cur.execute("DELETE FROM assassin_eliminations WHERE channel_id = %s", (channel_id,))

        players = mentioned_users
        random.shuffle(players)

        for i, player_id in enumerate(players):
            target_id = players[(i + 1) % len(players)]
            cur.execute(
                "INSERT INTO assassin_players (channel_id, player_id, target_id) VALUES (%s, %s, %s)",
                (channel_id, player_id, target_id)
            )

        conn.commit()

        player_names = ", ".join([f"<@{p}>" for p in players])
        say(f"A new game of Assassin has begun!\n*Players:* {player_names}\nEach player has been sent their first target via DM. Good luck!")

        print(f"--- Attempting to send targets for channel {channel_id} via DM ---")
        for player_id in players:
            try:
                cur.execute("SELECT target_id FROM assassin_players WHERE player_id = %s AND channel_id = %s", (player_id, channel_id))
                target_id_result = cur.fetchone()
                if not target_id_result:
                    print(f"🔴 DEBUG: Could not find target_id for player {player_id} in DB.")
                    continue

                target_id = target_id_result[0]
                target_name = get_user_name(target_id)
                print(f"--- DEBUG: Preparing DM for player {player_id} ({get_user_name(player_id)}) their target is {target_id} ({target_name}) ---")

                client.chat_postMessage(
                    channel=player_id,
                    text=f"Your first Assassin target in the <#{channel_id}> channel is: *{target_name}*."
                )
                print(f"--- DEBUG: Successfully sent DM to {player_id} ---")
            except Exception as e:
                print(f"🔴 DEBUG: Error sending DM to {player_id}: {e}")
                starter_name = get_user_name(starter_id)
                failed_player_name = get_user_name(player_id)
                say(f"⚠️ {starter_name}, I couldn't send a DM to {failed_player_name}. They might need to check their app permissions or start a conversation with me first.")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_start_command: {error}")
        say("Sorry, I ran into an error trying to start the game.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_target_command(message, say, client):
    channel_id = message['channel']
    player_id = message['user']

    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("SELECT target_id, is_active FROM assassin_players WHERE player_id = %s AND channel_id = %s", (player_id, channel_id))
        result = cur.fetchone()

        if not result:
            client.chat_postEphemeral(channel=channel_id, user=player_id, text="You are not currently in a game of Assassin in this channel.")
            return

        target_id, is_active = result
        if not is_active:
            client.chat_postEphemeral(channel=channel_id, user=player_id, text="You have been eliminated from the game!")
            return

        target_name = get_user_name(target_id)
        client.chat_postEphemeral(channel=channel_id, user=player_id, text=f"Your current target is: *{target_name}*.")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_target_command: {error}")
        client.chat_postEphemeral(channel=channel_id, user=player_id, text="Sorry, I had a problem fetching your target.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_eliminated_command(message, say, client):
    from bot import BOT_USER_ID

    print(f"\n--- DEBUG: handle_eliminated_command triggered by message: {message.get('text', '')[:50]} ---")
    print(f"--- DEBUG: Message user: {message.get('user')}, Bot ID: {BOT_USER_ID}, Message has bot_id: {'bot_id' in message} ---")

    if message.get('user') == BOT_USER_ID or message.get('bot_id') is not None:
        print("--- DEBUG: Ignoring message from self or another bot in handle_eliminated_command ---")
        return

    channel_id = message['channel']
    killer_id = message.get('user')
    if not killer_id:
        print("--- DEBUG: Message missing 'user' field in handle_eliminated_command. Skipping. ---")
        return

    text = message.get('text', '')

    if 'files' not in message:
        say("An elimination attempt requires photo or video proof!")
        return

    mentioned_users = re.findall(r"<@(\w+)>", text)
    if not mentioned_users:
        say("You must mention the player you are eliminating.")
        return
    victim_id = mentioned_users[0]

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("SELECT target_id, is_active FROM assassin_players WHERE player_id = %s AND channel_id = %s", (killer_id, channel_id))
        killer_data = cur.fetchone()

        if not killer_data:
            say("You are not a player in the current game.")
            return

        killer_target, killer_is_active = killer_data
        if not killer_is_active:
            say("You can't eliminate someone when you've already been eliminated!")
            return

        if killer_target != victim_id:
            say("That is not your target!")
            return

        cur.execute("SELECT target_id, is_active FROM assassin_players WHERE player_id = %s AND channel_id = %s", (victim_id, channel_id))
        victim_data = cur.fetchone()
        if not victim_data or not victim_data[1]:
            say("Your target has already been eliminated.")
            return

        new_target_id = victim_data[0]

        cur.execute("UPDATE assassin_players SET is_active = FALSE WHERE player_id = %s AND channel_id = %s", (victim_id, channel_id))
        cur.execute("UPDATE assassin_players SET target_id = %s, kill_count = kill_count + 1 WHERE player_id = %s AND channel_id = %s", (new_target_id, killer_id, channel_id))
        cur.execute("INSERT INTO assassin_eliminations (channel_id, killer_id, victim_id) VALUES (%s, %s, %s)", (channel_id, killer_id, victim_id))

        conn.commit()

        cur.execute("SELECT player_id FROM assassin_players WHERE channel_id = %s AND is_active = TRUE", (channel_id,))
        active_players = cur.fetchall()

        killer_name = get_user_name(killer_id)
        victim_name = get_user_name(victim_id)

        if len(active_players) == 1:
            winner_id = active_players[0][0]
            winner_name = get_user_name(winner_id)
            say(f"💥 *{killer_name}* has eliminated *{victim_name}*! 💥\n\n🏆 The game is over! Congratulations to the winner, *{winner_name}*! 🏆")
            cur.execute("DELETE FROM assassin_players WHERE channel_id = %s", (channel_id,))
            conn.commit()
        else:
            say(f"💥 *{killer_name}* has eliminated *{victim_name}*! 💥")
            new_target_name = get_user_name(new_target_id)
            client.chat_postEphemeral(
                channel=channel_id,
                user=killer_id,
                text=f"Congratulations on the elimination! Your new target is: *{new_target_name}*."
            )

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in eliminated_command: {error}")
        say("Sorry, I encountered an error while processing the elimination.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_alive_command(message, say):
    channel_id = message['channel']
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT player_id FROM assassin_players WHERE channel_id = %s AND is_active = TRUE ORDER BY created_at", (channel_id,))
        active_players_ids = [row[0] for row in cur.fetchall()]

        if not active_players_ids:
            say("No game is currently active, or everyone has been eliminated!")
            return

        alive_list = "\n".join([f"• {get_user_name(pid)}" for pid in active_players_ids])
        say(f"Players still alive:\n{alive_list}")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_alive_command: {error}")
        say("Sorry, I couldn't fetch the list of active players.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_dead_command(message, say):
    channel_id = message['channel']
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT ap.player_id, ae.killer_id, ae.created_at
            FROM assassin_players ap
            LEFT JOIN assassin_eliminations ae ON ap.player_id = ae.victim_id AND ap.channel_id = ae.channel_id
            WHERE ap.channel_id = %s AND ap.is_active = FALSE
            ORDER BY ae.created_at DESC NULLS LAST
            """, (channel_id,))
        eliminated_players_data = cur.fetchall()

        if not eliminated_players_data:
            say("No players have been eliminated yet in this game.")
            return

        dead_list_lines = []
        for victim_id, killer_id, eliminated_at in eliminated_players_data:
            victim_name = get_user_name(victim_id)
            if killer_id and eliminated_at:
                killer_name = get_user_name(killer_id)
                eliminated_at_str = eliminated_at.strftime("%Y-%m-%d %H:%M")
                dead_list_lines.append(f"• {victim_name} (eliminated by {killer_name} on {eliminated_at_str})")
            else:
                dead_list_lines.append(f"• {victim_name} (Eliminated)")

        dead_list = "\n".join(dead_list_lines)
        say(f"Players who have been eliminated:\n{dead_list}")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_dead_command: {error}")
        say("Sorry, I couldn't fetch the list of eliminated players.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_killcount_command(message, say):
    channel_id = message['channel']
    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT player_id, kill_count
            FROM assassin_players
            WHERE channel_id = %s AND kill_count > 0
            ORDER BY kill_count DESC
            LIMIT 3
            """, (channel_id,))
        top_killers = cur.fetchall()

        if not top_killers:
            say("No kills have been recorded yet in this game.")
            return

        killboard_lines = []
        for i, (player_id, kill_count) in enumerate(top_killers):
            player_name = get_user_name(player_id)
            killboard_lines.append(f"{i+1}. {player_name} - {kill_count} kills")

        killboard_text = "\n".join(killboard_lines)
        say(f"*Assassin Killboard (Top 3):*\n{killboard_text}")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_killcount_command: {error}")
        say("Sorry, I couldn't fetch the killboard.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_end_request(message, client, say):
    channel_id = message['channel']
    user_id = message['user']

    if user_id != ADMIN_USER_ID:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Sorry, only the designated admin can end an Assassin game."
        )
        return

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM assassin_players WHERE channel_id = %s AND is_active = TRUE", (channel_id,))
        active_game_count = cur.fetchone()[0]

        if active_game_count == 0:
            say("There is no active Assassin game in this channel to end.")
            return

        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Are you sure you want to end the current Assassin game? This cannot be undone.",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "Are you sure you want to end the current Assassin game? This will clear all game data for this channel."}},
                {"type": "actions", "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "Confirm End Game"}, "style": "danger", "action_id": "confirm_end_assassin_action"},
                    {"type": "button", "text": {"type": "plain_text", "text": "Cancel"}, "action_id": "cancel_end_assassin_action"}
                ]}
            ]
        )
    except Exception as e:
        print(f"🔴 Error sending end game confirmation: {e}")
        say("Sorry, I couldn't process the request to end the game.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_targets_command(message, client):
    channel_id = message['channel']
    user_id = message['user']

    if user_id != ADMIN_USER_ID:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Sorry, this is an admin-only command."
        )
        return

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("""
            SELECT player_id, target_id
            FROM assassin_players
            WHERE channel_id = %s AND is_active = TRUE
            ORDER BY created_at
            """, (channel_id,))
        targets = cur.fetchall()

        if not targets:
            client.chat_postMessage(channel=user_id, text=f"No active Assassin game found in <#{channel_id}>.")
            return

        target_list_lines = [f"*Current Assassin Targets in <#{channel_id}>:*"]
        for player_id, target_id in targets:
            player_name = get_user_name(player_id)
            target_name = get_user_name(target_id)
            target_list_lines.append(f"• {player_name} is targeting {target_name}")

        target_list_text = "\n".join(target_list_lines)
        client.chat_postMessage(channel=user_id, text=target_list_text)

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_targets_command: {error}")
        client.chat_postMessage(channel=user_id, text="Sorry, I encountered an error fetching the target list.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_remove_command(message, say, client):
    channel_id = message['channel']
    admin_id = message['user']
    text = message.get('text', '')

    if admin_id != ADMIN_USER_ID:
        client.chat_postEphemeral(
            channel=channel_id,
            user=admin_id,
            text="Sorry, only the designated admin can remove a player."
        )
        return

    mentioned_users = re.findall(r"<@(\w+)>", text)
    if not mentioned_users:
        say("You must mention the player you want to remove.")
        return
    player_to_remove_id = mentioned_users[0]

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("SELECT target_id FROM assassin_players WHERE player_id = %s AND channel_id = %s AND is_active = TRUE", (player_to_remove_id, channel_id))
        removed_player_data = cur.fetchone()
        if not removed_player_data:
            say(f"<@{player_to_remove_id}> is not an active player in this game.")
            return
        new_target_id = removed_player_data[0]

        cur.execute("SELECT player_id FROM assassin_players WHERE target_id = %s AND channel_id = %s AND is_active = TRUE", (player_to_remove_id, channel_id))
        targeter_data = cur.fetchone()

        if not targeter_data:
            print(f"--- WARNING: Could not find active player targeting {player_to_remove_id} in {channel_id}. Healing might be incomplete. ---")
        else:
            targeter_id = targeter_data[0]
            cur.execute("UPDATE assassin_players SET target_id = %s WHERE player_id = %s AND channel_id = %s", (new_target_id, targeter_id, channel_id))

        cur.execute("DELETE FROM assassin_players WHERE player_id = %s AND channel_id = %s", (player_to_remove_id, channel_id))
        conn.commit()

        removed_player_name = get_user_name(player_to_remove_id)
        say(f"Player *{removed_player_name}* has been removed from the game by admin.")

        if targeter_data:
            targeter_id = targeter_data[0]
            new_target_name = get_user_name(new_target_id)
            try:
                client.chat_postMessage(
                    channel=targeter_id,
                    text=f"Your previous target was removed from the game. Your new target in <#{channel_id}> is: *{new_target_name}*."
                )
            except Exception as dm_error:
                print(f"🔴 Error sending DM to {targeter_id} about new target: {dm_error}")
                say(f"⚠️ Could not DM <@{targeter_id}> about their new target.")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in assassin_remove_command: {error}")
        say("Sorry, I encountered an error trying to remove the player.")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def handle_assassin_help_command(message, say):
    help_text = """
*Assassin Game Commands:*
• `assassin target` or `mytarget`: Privately shows you your current target.
• `eliminated @target` (with image/video): Report that you have eliminated your target.
• `assassin alive`: Shows a list of players still in the game.
• `assassin dead`: Shows a list of eliminated players.
• `assassin killcount`: Displays the top 3 players by number of eliminations.
_Admin commands (`assassin start`, `assassin end`, `assassin targets`, `assassin remove`) are restricted._
"""
    say(help_text)


# --- Keyword Listeners ---

@app.message(re.compile(r"^assassin start", re.IGNORECASE))
def handle_assassin_start_keyword(message, say, client):
    handle_assassin_start_command(message, say, client)


@app.message(re.compile(r"^(assassin target|mytarget)$", re.IGNORECASE))
def handle_assassin_target_keyword(message, say, client):
    handle_assassin_target_command(message, say, client)


@app.message(re.compile(r"^(eliminated|eliminate)", re.IGNORECASE))
def handle_eliminated_keyword(message, say, client):
    handle_eliminated_command(message, say, client)


@app.message(re.compile(r"^assassin alive$", re.IGNORECASE))
def handle_assassin_alive_keyword(message, say):
    handle_assassin_alive_command(message, say)


@app.message(re.compile(r"^assassin dead$", re.IGNORECASE))
def handle_assassin_dead_keyword(message, say):
    handle_assassin_dead_command(message, say)


@app.message(re.compile(r"^assassin killcount$", re.IGNORECASE))
def handle_assassin_killcount_keyword(message, say):
    handle_assassin_killcount_command(message, say)


@app.message(re.compile(r"^assassin end$", re.IGNORECASE))
def handle_assassin_end_keyword(message, client, say):
    handle_assassin_end_request(message, client, say)


@app.message(re.compile(r"^assassin targets$", re.IGNORECASE))
def handle_assassin_targets_keyword(message, client):
    handle_assassin_targets_command(message, client)


@app.message(re.compile(r"^assassin remove", re.IGNORECASE))
def handle_assassin_remove_keyword(message, say, client):
    handle_assassin_remove_command(message, say, client)


@app.message(re.compile(r"^assassin help$", re.IGNORECASE))
def handle_assassin_help_keyword(message, say):
    handle_assassin_help_command(message, say)


# --- Action Listeners ---

@app.action("confirm_end_assassin_action")
def handle_confirm_end_action(ack, body, client, say):
    ack()

    channel_id = body['channel']['id']
    user_id = body['user']['id']
    message_ts = body['container']['message_ts']

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()

        print(f"--- DEBUG: Attempting to DELETE game data for channel {channel_id} ---")
        cur.execute("DELETE FROM assassin_players WHERE channel_id = %s", (channel_id,))
        players_deleted = cur.rowcount
        cur.execute("DELETE FROM assassin_eliminations WHERE channel_id = %s", (channel_id,))
        eliminations_deleted = cur.rowcount

        conn.commit()
        print(f"--- DEBUG: DELETEd {players_deleted} players and {eliminations_deleted} eliminations ---")

        say(f"🛑 The Assassin game in this channel has been manually ended by <@{user_id}>.")
        print(f"--- ASSASSIN GAME ENDED in channel {channel_id} by user {user_id} ---")

        client.chat_delete(
            channel=channel_id,
            ts=message_ts
        )

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error in confirm_end_assassin_action: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
        print("--- DEBUG: Database connection closed in confirm_end_assassin_action ---")


@app.action("cancel_end_assassin_action")
def handle_cancel_end_action(ack, body, client):
    ack()

    channel_id = body['channel']['id']
    message_ts = body['container']['message_ts']
    try:
        client.chat_delete(
            channel=channel_id,
            ts=message_ts
        )
    except Exception as e:
        print(f"🔴 Error in cancel_end_assassin_action: {e}")
