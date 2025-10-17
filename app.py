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
    app.run(host="0.0.0.0", port=3000)
🧩 STEP 3: Create the HTML template
Inside your project, create a folder called templates

Inside templates, create index.html:

html
Copy code
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ bot_name }} Dashboard</title>
  <style>
    body { background-color: #0b0c10; color: #66fcf1; font-family: 'Poppins', sans-serif; margin: 0; padding: 0; }
    header { background: #1f2833; padding: 20px; text-align: center; color: #45a29e; font-size: 2em; }
    main { max-width: 800px; margin: 30px auto; background: #1f2833; padding: 25px; border-radius: 12px; box-shadow: 0 0 15px #45a29e; }
    h2 { color: #66fcf1; border-bottom: 2px solid #45a29e; padding-bottom: 5px; }
    ul { list-style: none; padding: 0; }
    li { margin: 10px 0; background: #0b0c10; padding: 10px; border-radius: 8px; box-shadow: 0 0 8px #45a29e; }
    a { color: #66fcf1; text-decoration: none; font-weight: bold; }
    a:hover { color: #45a29e; }
    footer { text-align: center; padding: 15px; color: #45a29e; font-size: 0.9em; }
  </style>
</head>
<body>
  <header>🌌 {{ bot_name }} Dashboard</header>
  <main>
    <section>
      <h2>About {{ bot_name }}</h2>
      <p>
        {{ bot_name }} is a multi-purpose Discord bot designed to make your community safer, smarter, and more fun.
      </p>
      <p><strong>Server Link:</strong> <a href="{{ server_link }}" target="_blank">Join the Official Server</a></p>
      <p><strong>Bot Uptime:</strong> {{ uptime }}</p>
    </section>

    <section>
      <h2>Public Commands</h2>
      <ul>
        {% for cmd in commands %}
        <li><strong>{{ cmd.name }}</strong> — {{ cmd.desc }}</li>
        {% endfor %}
      </ul>
    </section>

    <section>
      <h2>Bot Features</h2>
      <ul>
        {% for feat in features %}
        <li>✅ {{ feat }}</li>
        {% endfor %}
      </ul>
    </section>
  </main>
  <footer>
    © 2025 {{ bot_name }} | Created by the Life Bot Team
  </footer>
</body>
</html>