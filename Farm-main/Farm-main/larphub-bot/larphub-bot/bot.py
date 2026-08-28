import os
import secrets
import string
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, make_response, render_template_string
import discord
from discord.ext import commands
from supabase import create_client

TOKEN = os.environ["DISCORD_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ADMIN_ROLE_ID = int(os.environ["ADMIN_ROLE_ID"])
PORT = int(os.environ.get("PORT", 10000))

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

def generate_key_string():
    chars = string.ascii_uppercase + string.digits
    part1 = "".join(secrets.choice(chars) for _ in range(4))
    part2 = "".join(secrets.choice(chars) for _ in range(4))
    return f"LARP-{part1}-{part2}"

def get_client_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr

KEY_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Larp Hub - 24-Hour Key</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body {
            background-color: #120808;
            color: #ffffff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }
        .card {
            background-color: #1f0d0d;
            border: 2px solid #bc3e3e;
            border-radius: 16px;
            padding: 36px 30px;
            max-width: 440px;
            width: 100%;
            text-align: center;
            box-shadow: 0 10px 30px rgba(188, 62, 62, 0.25);
        }
        h1 { color: #bc3e3e; font-size: 28px; margin-bottom: 8px; }
        .tag {
            display: inline-block;
            background: #381212;
            color: #ff8585;
            font-size: 12px;
            padding: 4px 12px;
            border-radius: 20px;
            margin-bottom: 20px;
            font-weight: bold;
        }
        p { color: #d6b4b4; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .key-container {
            background-color: #2b1111;
            border: 1px dashed #bc3e3e;
            border-radius: 10px;
            padding: 16px;
            font-size: 20px;
            font-weight: bold;
            color: #ffffff;
            letter-spacing: 2px;
            margin-bottom: 20px;
            user-select: all;
            word-break: break-all;
        }
        .btn {
            background-color: #bc3e3e;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            padding: 12px 24px;
            font-size: 15px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            transition: 0.2s ease;
        }
        .btn:hover { background-color: #d64a4a; }
        .note {
            margin-top: 18px;
            font-size: 12px;
            color: #997575;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>Larp Hub</h1>
        <div class="tag">{{ tag_text }}</div>
        <p>{{ message_text }}</p>
        <div class="key-container" id="keyText">{{ key }}</div>
        <button class="btn" onclick="copyKey()">Copy Key</button>
        <div class="note">Paste this key into Larp Hub in Roblox to execute.</div>
    </div>
    <script>
        function copyKey() {
            var text = document.getElementById("keyText").innerText.trim();
            navigator.clipboard.writeText(text).then(function() {
                var btn = document.querySelector(".btn");
                btn.innerText = "Copied to Clipboard!";
                btn.style.backgroundColor = "#2e8b57";
                setTimeout(function() {
                    btn.innerText = "Copy Key";
                    btn.style.backgroundColor = "#bc3e3e";
                }, 2000);
            });
        }
    </script>
</body>
</html>
"""

@app.route("/getkey", methods=["GET"])
def get_key_page():
    now_iso = datetime.now(timezone.utc).isoformat()
    client_ip = get_client_ip()
    cookie_key = request.cookies.get("larp_active_key")
    
    active_key_data = None
    
    if cookie_key:
        res = sb.table("keys").select("*").eq("key", cookie_key).gt("expires_at", now_iso).execute()
        if res.data:
            active_key_data = res.data[0]
            
    if not active_key_data and client_ip:
        res = sb.table("keys").select("*").eq("ip_address", client_ip).gt("expires_at", now_iso).order("created_at", desc=True).limit(1).execute()
        if res.data:
            active_key_data = res.data[0]
            
    if active_key_data:
        key = active_key_data["key"]
        expires_at = datetime.fromisoformat(active_key_data["expires_at"].replace("Z", "+00:00"))
        remaining = expires_at - datetime.now(timezone.utc)
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        
        tag_text = f"ACTIVE KEY ({hours}h {minutes}m remaining)"
        message_text = "You already have an active 24-hour key. You don't need to do any more checkpoints until this key expires!"
        
        response = make_response(render_template_string(
            KEY_PAGE_HTML,
            key=key,
            tag_text=tag_text,
            message_text=message_text
        ))
        response.set_cookie("larp_active_key", key, max_age=int(remaining.total_seconds()), httponly=True, samesite="Lax")
        return response

    new_key = generate_key_string()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    
    sb.table("keys").insert({
        "key": new_key,
        "ip_address": client_ip,
        "expires_at": expires_at.isoformat(),
        "created_at": now_iso,
        "roblox_userid": None
    }).execute()
    
    response = make_response(render_template_string(
        KEY_PAGE_HTML,
        key=new_key,
        tag_text="NEW 24-HOUR KEY",
        message_text="Thank you for completing the checkpoint! Here is your fresh 24-hour key:"
    ))
    response.set_cookie("larp_active_key", new_key, max_age=86400, httponly=True, samesite="Lax")
    return response


@app.route("/redeem", methods=["POST"])
def redeem_key():
    """Validates key and locks it to the player's Roblox UserID"""
    data = request.json or {}
    key = data.get("key", "").strip()
    roblox_userid = str(data.get("roblox_userid", "")).strip()

    if not key or not roblox_userid:
        return jsonify({"valid": False, "message": "Missing key or userid"}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    res = sb.table("keys").select("*").eq("key", key).gt("expires_at", now_iso).execute()
    
    if not res.data:
        return jsonify({"valid": False, "message": "Key is invalid or expired"}), 200

    record = res.data[0]

    if record.get("roblox_userid") and record["roblox_userid"] != roblox_userid:
        return jsonify({"valid": False, "message": "This key is bound to another Roblox account"}), 200

    if not record.get("roblox_userid"):
        sb.table("keys").update({"roblox_userid": roblox_userid}).eq("key", key).execute()

    return jsonify({"valid": True, "message": "Key accepted!"}), 200


@app.route("/check", methods=["POST"])
def check_key():
    """Checks if a saved key is still valid and owned by the player"""
    data = request.json or {}
    key = data.get("key", "").strip()
    roblox_userid = str(data.get("roblox_userid", "")).strip()

    if not key or not roblox_userid:
        return jsonify({"valid": False}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    res = sb.table("keys").select("*").eq("key", key).eq("roblox_userid", roblox_userid).gt("expires_at", now_iso).execute()

    if res.data:
        return jsonify({"valid": True}), 200
    return jsonify({"valid": False}), 200


@app.route("/")
def index():
    return "Larp Hub API is online."

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(ctx):
    return any(r.id == ADMIN_ROLE_ID for r in ctx.author.roles)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.command()
async def whitelist(ctx, roblox_userid: str, *, note: str = ""):
    if not is_admin(ctx):
        return await ctx.send("No permission.")
    sb.table("whitelist").upsert({
        "roblox_userid": str(roblox_userid),
        "discord_tag": str(ctx.author),
        "note": note
    }).execute()
    await ctx.send(f"✅ Whitelisted Roblox User ID `{roblox_userid}`.")

@bot.command()
async def unwhitelist(ctx, roblox_userid: str):
    if not is_admin(ctx):
        return await ctx.send("No permission.")
    sb.table("whitelist").delete().eq("roblox_userid", str(roblox_userid)).execute()
    await ctx.send(f"❌ Removed `{roblox_userid}` from whitelist.")

@bot.command()
async def genkey(ctx, hours: int = 24):
    if not is_admin(ctx):
        return await ctx.send("No permission.")
    new_key = generate_key_string()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    sb.table("keys").insert({
        "key": new_key,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "roblox_userid": None
    }).execute()
    await ctx.send(f"🔑 Generated custom key: `{new_key}` (Valid for {hours}h)")

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    bot.run(TOKEN)
