from flask import Flask, render_template
from datetime import datetime
import pytz

app = Flask(__name__)

# ---------- BOT INFO ----------
bot_name = "Life Bot"
server_link = "https://discord.gg/Z8uufBTW"
start_time = datetime.now(pytz.UTC)

public_commands = [
    {"name": "!help", "desc": "Shows all available commands"},
    {"name": "!info", "desc": "Gives bot and server information"},
    {"name": "!ping", "desc": "Checks bot latency"},
    {"name": "!user", "desc": "Displays information about a user"},
    {"name": "!server", "desc": "Shows server statistics"},
    {"name": "!clear [amount]", "desc": "Deletes messages"},
    {"name": "!avatar [@user]", "desc": "Shows a user's avatar"},
    {"name": "!uptime", "desc": "Shows how long the bot has been running"}
]

features = [
    "Smart moderation (kick, ban, warn, mute)",
    "Custom prefixes and setup commands",
    "Fun & utility tools for daily use",
    "Activity tracking and uptime monitoring",
    "Security-first design to prevent abuse"
]

# ---------- ROUTES ----------
@app.route("/")
def index():
    # Calculate uptime
    now = datetime.now(pytz.UTC)
    uptime = now - start_time
    return render_template("index.html",
                           bot_name=bot_name,
                           server_link=server_link,
                           commands=public_commands,
                           features=features,
                           uptime=str(uptime).split(".")[0])  # format as H:M:S

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)