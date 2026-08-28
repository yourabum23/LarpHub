import os
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template_string
import discord
from discord.ext import commands
from supabase import create_client

TOKEN = os.environ["DISCORD_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ADMIN_ROLE_ID = int(os.environ["ADMIN_ROLE_ID"])
PORT = int(os.environ.get("PORT", 10000))

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_admin(ctx):
    return any(r.id == ADMIN_ROLE_ID for r in ctx.author.roles)


def generate_random_key():
    chars = string.ascii_uppercase + string.digits
    part1 = "".join(secrets.choice(chars) for _ in range(4))
    part2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"LARP-{part1}-{part2}"


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")


# ==================== DISCORD BOT COMMANDS ====================

@bot.command()
async def whitelist(ctx, roblox_userid: str, *, note: str = ""):
    """!whitelist 123456789 gold plan"""
    if not is_admin(ctx):
        return await ctx.send("No permission.")

    data = {
        "roblox_userid": str(roblox_userid),
        "discord_tag": str(ctx.author),
        "note": note,
        "added_by": str(ctx.author.id),
    }
    result = sb.table("whitelist").upsert(data, on_conflict="roblox_userid").execute()

    if result.data:
        await ctx.send(f"Whitelisted `{roblox_userid}` permanently.")
    else:
        await ctx.send("Failed to write to Supabase.")


@bot.command()
async def unwhitelist(ctx, roblox_userid: str):
    """!unwhitelist 123456789"""
    if not is_admin(ctx):
        return await ctx.send("No permission.")

    result = sb.table("whitelist").delete().eq("roblox_userid", str(roblox_userid)).execute()
    if result.data:
        await ctx.send(f"Removed `{roblox_userid}` from whitelist.")
    else:
        await ctx.send(f"`{roblox_userid}` was not on the whitelist.")


@bot.command()
async def genkey(ctx, hours: int = 24):
    """!genkey 24 (Admin manual key generator)"""
    if not is_admin(ctx):
        return await ctx.send("No permission.")

    new_key = generate_random_key()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    data = {
        "key": new_key,
        "expires_at": expires_at,
    }
    sb.table("keys").insert(data).execute()
    await ctx.send(f"Generated Key (valid for {hours}h):\n`{new_key}`")


# ==================== FLASK KEY API & LINKVERTISE GATEWAY ====================

app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Larp Hub - Your 24-Hour Key</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background: #120808; color: #fff; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: #1c0d0d; border: 2px solid #bc3e3e; border-radius: 12px; padding: 30px; text-align: center; max-width: 400px; width: 90%; box-shadow: 0 8px 24px rgba(0,0,0,0.6); }
        h1 { color: #bc3e3e; margin-bottom: 10px; }
        p { color: #dcb4b4; font-size: 14px; }
        .key-box { background: #641414; padding: 15px; border-radius: 8px; font-size: 20px; font-weight: bold; letter-spacing: 2px; margin: 20px 0; user-select: all; word-break: break-all; }
        .btn { background: #bc3e3e; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn:hover { background: #d44d4d; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Larp Hub</h1>
        <p>Thank you for completing the checkpoint! Here is your 24-hour key:</p>
        <div class="key-box" id="keyText">{{ key }}</div>
        <button class="btn" onclick="copyKey()">Copy Key</button>
    </div>
    <script>
        function copyKey() {
            var text = document.getElementById('keyText').innerText;
            navigator.clipboard.writeText(text);
            alert('Key copied to clipboard!');
        }
    </script>
</body>
</html>
"""

@app.get("/")
def home():
    return "LarpHub bot online", 200

@app.get("/health")
def health():
    return {"ok": True, "bot": str(bot.user) if bot.user else None}, 200

@app.get("/getkey")
def getkey():
    new_key = generate_random_key()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    sb.table("keys").insert({"key": new_key, "expires_at": expires_at}).execute()
    return render_template_string(HTML_PAGE, key=new_key)

@app.post("/redeem")
def redeem_key():
    req_data = request.json or {}
    key = str(req_data.get("key", "")).strip()
    user_id = str(req_data.get("roblox_userid", "")).strip()

    if not key or not user_id:
        return jsonify({"valid": False, "message": "Missing key or UserId"}), 400

    res = sb.table("keys").select("*").eq("key", key).execute()
    if not res.data:
        return jsonify({"valid": False, "message": "Invalid key"}), 200

    key_record = res.data[0]
    expires_at = datetime.fromisoformat(key_record["expires_at"].replace("Z", "+00:00"))

    if datetime.now(timezone.utc) > expires_at:
        return jsonify({"valid": False, "message": "Key has expired (24h limit reached)"}), 200

    bound_user = key_record.get("roblox_userid")
    if bound_user and bound_user != user_id:
        return jsonify({"valid": False, "message": "Key is locked to another user!"}), 200

    if not bound_user:
        sb.table("keys").update({"roblox_userid": user_id}).eq("key", key).execute()

    return jsonify({"valid": True, "message": "Key valid!"}), 200

@app.post("/check")
def check_key():
    req_data = request.json or {}
    key = str(req_data.get("key", "")).strip()
    user_id = str(req_data.get("roblox_userid", "")).strip()

    res = sb.table("keys").select("*").eq("key", key).execute()
    if not res.data:
        return jsonify({"valid": False}), 200

    key_record = res.data[0]
    expires_at = datetime.fromisoformat(key_record["expires_at"].replace("Z", "+00:00"))

    if datetime.now(timezone.utc) > expires_at:
        return jsonify({"valid": False}), 200

    if key_record.get("roblox_userid") != user_id:
        return jsonify({"valid": False}), 200

    return jsonify({"valid": True}), 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
