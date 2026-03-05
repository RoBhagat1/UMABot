import os
from dotenv import load_dotenv
from slack_bolt import App

load_dotenv()

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
BOT_USER_ID = app.client.auth_test()["user_id"]
