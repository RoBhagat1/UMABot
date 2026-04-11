import os
import psycopg2


def setup_database():
    spots_table_command = """
    CREATE TABLE IF NOT EXISTS spots (
        id SERIAL PRIMARY KEY,
        spotter_id TEXT NOT NULL,
        spotted_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_ts TEXT NOT NULL,
        image_url TEXT NOT NULL,
        spotter_points INTEGER NOT NULL DEFAULT 1,
        caught_points INTEGER NOT NULL DEFAULT 1,
        season_id TEXT NOT NULL,
        is_valid BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (message_ts, spotted_id)
    );
    """
    assassin_players_table_command = """
    CREATE TABLE IF NOT EXISTS assassin_players (
        id SERIAL PRIMARY KEY,
        channel_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        kill_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    assassin_eliminations_table_command = """
    CREATE TABLE IF NOT EXISTS assassin_eliminations (
        id SERIAL PRIMARY KEY,
        channel_id TEXT NOT NULL,
        killer_id TEXT NOT NULL,
        victim_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    channel_seasons_table_command = """
    CREATE TABLE IF NOT EXISTS channel_seasons (
        id SERIAL PRIMARY KEY,
        channel_id TEXT NOT NULL,
        season_start TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    conn = None
    cur = None
    try:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("🔴 DATABASE_URL is not set. Please check your .env file.")
            return
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(spots_table_command)
        cur.execute(assassin_players_table_command)
        cur.execute(assassin_eliminations_table_command)
        cur.execute(channel_seasons_table_command)
        conn.commit()
        print("✅ All database tables are ready.")
    except (Exception, psycopg2.DatabaseError) as error:
        print(f"🔴 Error while connecting to PostgreSQL: {error}")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()
