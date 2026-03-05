import pytz
from datetime import datetime

ADMIN_USER_ID = "U06HB636NHG"
EXPLOSIONS_DIR = "explosions"
SEASON_START_DATE = datetime(2025, 10, 9, 0, 0, 0, tzinfo=pytz.timezone('America/Los_Angeles'))

# Shared mutable state — mutate these dicts in place to keep references valid across modules
user_cache = {}
daily_bonus_users = {}
manual_reset_timestamps = {}
