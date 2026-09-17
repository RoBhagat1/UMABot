import os
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot import app
from database import setup_database
from jobs import daily_bonus_job, birthday_job, internship_digest_job
import handlers.spot  # noqa: F401 — registers @app listeners
import handlers.assassin  # noqa: F401 — registers @app listeners
import handlers.birthday  # noqa: F401 — registers @app listeners
import handlers.internships  # noqa: F401 — registers @app listeners

if __name__ == "__main__":
    setup_database()

    scheduler = BackgroundScheduler(timezone=pytz.timezone('America/Los_Angeles'))
    # daily_bonus_job disabled — re-add this line to bring the 2x daily bonus back
    # scheduler.add_job(daily_bonus_job, 'cron', hour=0, minute=0)
    scheduler.add_job(birthday_job, 'cron', hour=9, minute=45)
    scheduler.add_job(internship_digest_job, 'cron', hour=12, minute=0)
    # Slack's Socket Mode connection has been observed to silently stop
    # receiving events after some hours, with no crash and no error logged
    # — the process stays "alive" but the bot becomes fully unresponsive.
    # A forced periodic restart is a defensive workaround: Fly restarts the
    # machine automatically whenever the process exits, so exiting here on
    # a schedule guarantees the connection gets refreshed periodically
    # rather than staying silently dead until someone notices and manually
    # restarts it.
    scheduler.add_job(lambda: os._exit(1), 'interval', hours=4)
    scheduler.start()
    print("⏰ Scheduler started. All jobs are scheduled.")

    print("⚡️ Spot Bot is running!")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
