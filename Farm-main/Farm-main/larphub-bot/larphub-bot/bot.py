import os
import secrets
import string
import threading
import asyncio
import requests
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, make_response, render_template_string
import discord
from discord.ext import commands
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.environ["DISCORD_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ADMIN_ROLE_ID = int(os.environ["ADMIN_ROLE_ID"])
WORKINK_API_KEY = os.environ["WORKINK_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))
WORKINK_ENTRY_URL = os.environ.get("WORKINK_ENTRY_URL", "https://work.ink/2U7C/larp-hub-key")
KEY_LOG_CHANNEL_ID = int(os.environ.get("KEY_LOG_CHANNEL_ID", 0))
SCRIPT_LOADSTRING = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/yourabum23/LarpHub/refs/heads/main/Farm-main/Farm-main/larphub-bot/larphub-bot/LarpHub.lua"))()'

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
app = Flask(__name__)
bot_loop: asyncio.AbstractEventLoop | None = None

# ── Rate Limiting ─────────────────────────────────────────────────────────────
_rl_lock = threading.Lock()
_rl_attempts: dict[str, list[float]] = defaultdict(list)
RL_WINDOW = 60   # seconds
RL_MAX = 5       # max /redeem attempts per IP per window

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _rl_lock:
        _rl_attempts[ip] = [t for t in _rl_attempts[ip] if now - t < RL_WINDOW]
        if len(_rl_attempts[ip]) >= RL_MAX:
            return True
        _rl_attempts[ip].append(now)
        return False

# ── Helpers ──────────────────────────────────────────────────────────────────
def generate_key_string():
    chars = string.ascii_uppercase + string.digits
    return "LARP-" + "".join(secrets.choice(chars) for _ in range(4)) + "-" + "".join(secrets.choice(chars) for _ in range(4))

def generate_referral_code():
    chars = string.ascii_uppercase + string.digits
    return "LREF-" + "".join(secrets.choice(chars) for _ in range(6))

def get_client_ip():
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else request.remote_addr

def verify_workink_token(token: str, client_ip: str) -> tuple[bool, str]:
    if not token:
        return False, "missing_token"
    try:
        resp = requests.get(
            f"https://work.ink/_api/v2/token/verify/{token}",
            headers={"X-Api-Key": WORKINK_API_KEY},
            params={"deleteToken": 1},
            timeout=5,
        )
    except requests.RequestException:
        return False, "network_error"
    if resp.status_code == 401: return False, "bad_api_key"
    if resp.status_code == 403: return False, "wrong_account"
    if resp.status_code != 200: return False, "unexpected_status"
    try:
        data = resp.json()
    except ValueError:
        return False, "bad_json"
    if not data.get("valid"):
        return False, "invalid_or_used"
    token_ip = (data.get("info") or {}).get("byIp")
    if token_ip and len(token_ip) <= 45 and token_ip != client_ip:
        return False, "ip_mismatch"
    return True, "ok"

def schedule_log(title: str, description: str, color: int = 0xbc3e3e):
    """Post a log embed to the key logs Discord channel from any thread."""
    if not bot_loop or not KEY_LOG_CHANNEL_ID:
        return
    async def _send():
        ch = bot.get_channel(KEY_LOG_CHANNEL_ID)
        if not ch:
            return
        embed = discord.Embed(title=title, description=description,
                              color=discord.Color(color), timestamp=datetime.now(timezone.utc))
        try:
            await ch.send(embed=embed)
        except Exception:
            pass
    asyncio.run_coroutine_threadsafe(_send(), bot_loop)

def handle_referral(referral_code: str, new_user_roblox_id: str):
    """Credit a referral use. Every 3 referrals = 48hr bonus key for the owner."""
    if not referral_code:
        return
    res = sb.table("referral_codes").select("*").eq("code", referral_code).execute()
    if not res.data:
        return
    record = res.data[0]
    owner = record["owner_roblox_userid"]
    if owner == new_user_roblox_id:  # Can't refer yourself
        return
    new_count = record["use_count"] + 1
    bonus_given = record.get("bonus_keys_given", 0)
    sb.table("referral_codes").update({"use_count": new_count}).eq("code", referral_code).execute()
    bonuses_owed = new_count // 3
    if bonuses_owed > bonus_given:
        bonus_key = generate_key_string()
        bonus_expires = datetime.now(timezone.utc) + timedelta(hours=48)
        sb.table("keys").insert({
            "key": bonus_key,
            "roblox_userid": owner,
            "expires_at": bonus_expires.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ip_address": None,
            "hwid": None,
            "blacklisted": False,
            "hwid_reset_used": False,
        }).execute()
        sb.table("referral_codes").update({"bonus_keys_given": bonuses_owed}).eq("code", referral_code).execute()
        schedule_log(
            "🎁 Referral Bonus Key Issued",
            f"Owner Roblox ID: `{owner}`\nCode: `{referral_code}`\nTotal referrals: `{new_count}`\nBonus key: `{bonus_key}` (48h)",
            color=0x2e8b57,
        )

# ── HTML Templates ────────────────────────────────────────────────────────────
KEY_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Larp Hub - 24-Hour Key</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #120808; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background-color: #1f0d0d; border: 2px solid #bc3e3e; border-radius: 16px; padding: 36px 30px; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(188,62,62,0.25); }
        h1 { color: #bc3e3e; font-size: 28px; margin-bottom: 8px; }
        .tag { display: inline-block; background: #381212; color: #ff8585; font-size: 12px; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px; font-weight: bold; }
        p { color: #d6b4b4; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .key-container { background-color: #2b1111; border: 1px dashed #bc3e3e; border-radius: 10px; padding: 16px; font-size: 20px; font-weight: bold; color: #fff; letter-spacing: 2px; margin-bottom: 20px; user-select: all; word-break: break-all; }
        .btn { background-color: #bc3e3e; color: #fff; border: none; border-radius: 8px; padding: 12px 24px; font-size: 15px; font-weight: bold; cursor: pointer; width: 100%; transition: 0.2s ease; }
        .btn:hover { background-color: #d64a4a; }
        .note { margin-top: 18px; font-size: 12px; color: #997575; }
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
            navigator.clipboard.writeText(document.getElementById("keyText").innerText.trim()).then(function() {
                var btn = document.querySelector(".btn");
                btn.innerText = "Copied!"; btn.style.backgroundColor = "#2e8b57";
                setTimeout(function() { btn.innerText = "Copy Key"; btn.style.backgroundColor = "#bc3e3e"; }, 2000);
            });
        }
    </script>
</body>
</html>
"""

ERROR_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Larp Hub - Checkpoint Required</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #120808; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background-color: #1f0d0d; border: 2px solid #bc3e3e; border-radius: 16px; padding: 36px 30px; max-width: 440px; width: 100%; text-align: center; }
        h1 { color: #bc3e3e; font-size: 24px; margin-bottom: 16px; }
        p { color: #d6b4b4; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .btn { display: inline-block; background-color: #bc3e3e; color: #fff; text-decoration: none; border-radius: 8px; padding: 12px 24px; font-size: 15px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="card">
        <h1>{{ heading }}</h1>
        <p>{{ message_text }}</p>
        <a class="btn" href="{{ workink_url }}">Start Checkpoint</a>
    </div>
</body>
</html>
"""

# ── Flask Routes ──────────────────────────────────────────────────────────────

@app.route("/getkey", methods=["GET"])
def get_key_page():
    now_iso = datetime.now(timezone.utc).isoformat()
    client_ip = get_client_ip()
    cookie_key = request.cookies.get("larp_active_key")
    token = request.args.get("token")
    active_key_data = None

    if cookie_key:
        res = sb.table("keys").select("*").eq("key", cookie_key).gt("expires_at", now_iso).execute()
        if res.data and not res.data[0].get("blacklisted"):
            active_key_data = res.data[0]

    if not active_key_data and client_ip:
        res = sb.table("keys").select("*").eq("ip_address", client_ip).gt("expires_at", now_iso).order("created_at", desc=True).limit(1).execute()
        if res.data and not res.data[0].get("blacklisted"):
            active_key_data = res.data[0]

    if active_key_data:
        key = active_key_data["key"]
        expires_at = datetime.fromisoformat(active_key_data["expires_at"].replace("Z", "+00:00"))
        remaining = expires_at - datetime.now(timezone.utc)
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        response = make_response(render_template_string(
            KEY_PAGE_HTML,
            key=key,
            tag_text=f"ACTIVE KEY ({hours}h {minutes}m remaining)",
            message_text="You already have an active 24-hour key. You don't need to do the checkpoint again until it expires!"
        ))
        response.set_cookie("larp_active_key", key, max_age=int(remaining.total_seconds()), httponly=True, samesite="Lax")
        return response

    if not token:
        return render_template_string(ERROR_PAGE_HTML, heading="Checkpoint Required",
            message_text="You need to complete the checkpoint before you can get a key.",
            workink_url=WORKINK_ENTRY_URL), 400

    ok, reason = verify_workink_token(token, client_ip)
    if not ok:
        messages = {
            "invalid_or_used": "This checkpoint link is invalid, expired, or already redeemed. Please complete a new checkpoint.",
            "ip_mismatch": "This checkpoint was completed from a different network. You must complete the checkpoint yourself.",
        }
        msg = messages.get(reason, "We couldn't verify that checkpoint. Please complete it again.")
        if reason in ("bad_api_key", "wrong_account"):
            app.logger.error(f"Work.ink config error: {reason}")
            msg = "Something's misconfigured on our end. Please try again or contact staff."
        return render_template_string(ERROR_PAGE_HTML, heading="Checkpoint Verification Failed",
            message_text=msg, workink_url=WORKINK_ENTRY_URL), 400

    new_key = generate_key_string()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    now_iso2 = datetime.now(timezone.utc).isoformat()
    sb.table("keys").insert({
        "key": new_key, "ip_address": client_ip,
        "expires_at": expires_at.isoformat(), "created_at": now_iso2,
        "roblox_userid": None, "hwid": None, "blacklisted": False, "hwid_reset_used": False,
    }).execute()

    schedule_log("🔑 New Key Generated",
        f"Key: `{new_key}`\nIP: `{client_ip}`\nExpires: <t:{int(expires_at.timestamp())}:R>")

    response = make_response(render_template_string(KEY_PAGE_HTML, key=new_key,
        tag_text="NEW 24-HOUR KEY",
        message_text="Thank you for completing the checkpoint! Here is your fresh 24-hour key:"))
    response.set_cookie("larp_active_key", new_key, max_age=86400, httponly=True, samesite="Lax")
    return response


@app.route("/redeem", methods=["POST"])
def redeem_key():
    client_ip = get_client_ip()
    if is_rate_limited(client_ip):
        return jsonify({"valid": False, "message": "Too many attempts. Please wait a minute."}), 429

    data = request.json or {}
    key = data.get("key", "").strip()
    roblox_userid = str(data.get("roblox_userid", "")).strip()
    hwid = data.get("hwid", "").strip()
    referral_code = data.get("referral_code", "").strip()

    if not key or not roblox_userid:
        return jsonify({"valid": False, "message": "Missing key or userid"}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    res = sb.table("keys").select("*").eq("key", key).gt("expires_at", now_iso).execute()
    if not res.data:
        return jsonify({"valid": False, "message": "Key is invalid or expired"}), 200

    record = res.data[0]

    if record.get("blacklisted"):
        return jsonify({"valid": False, "message": "This key has been blacklisted."}), 200

    if record.get("roblox_userid") and record["roblox_userid"] != roblox_userid:
        return jsonify({"valid": False, "message": "This key is bound to another Roblox account."}), 200

    if record.get("hwid") and hwid and record["hwid"] != hwid:
        return jsonify({"valid": False, "message": "This key is locked to a different device. Use the 🖥️ Reset HWID button in our Discord."}), 200

    # Bind Roblox user + HWID on first use
    update = {}
    first_bind = not record.get("roblox_userid")
    if first_bind:
        update["roblox_userid"] = roblox_userid
    if not record.get("hwid") and hwid:
        update["hwid"] = hwid
    if update:
        sb.table("keys").update(update).eq("key", key).execute()

    # Only credit referral on the very first bind
    if referral_code and first_bind:
        handle_referral(referral_code, roblox_userid)

    schedule_log("✅ Key Redeemed",
        f"Key: `{key}`\nRoblox ID: `{roblox_userid}`\nHWID: `{hwid or 'N/A'}`\nIP: `{client_ip}`",
        color=0x2e8b57)

    return jsonify({"valid": True, "message": "Key accepted!"}), 200


@app.route("/check", methods=["POST"])
def check_key():
    data = request.json or {}
    key = data.get("key", "").strip()
    roblox_userid = str(data.get("roblox_userid", "")).strip()
    hwid = data.get("hwid", "").strip()

    if not key or not roblox_userid:
        return jsonify({"valid": False}), 400

    now_iso = datetime.now(timezone.utc).isoformat()
    res = sb.table("keys").select("*").eq("key", key).eq("roblox_userid", roblox_userid).gt("expires_at", now_iso).execute()
    if not res.data:
        return jsonify({"valid": False}), 200

    record = res.data[0]
    if record.get("blacklisted"):
        return jsonify({"valid": False, "message": "blacklisted"}), 200
    if hwid and record.get("hwid") and record["hwid"] != hwid:
        return jsonify({"valid": False, "message": "hwid_mismatch"}), 200

    return jsonify({"valid": True}), 200


@app.route("/")
def index():
    return "Larp Hub API is online."


# ── Discord UI Components ────────────────────────────────────────────────────

class ResetHWIDModal(discord.ui.Modal, title="Reset HWID"):
    key_input = discord.ui.TextInput(
        label="Your Key",
        placeholder="LARP-XXXX-XXXX",
        min_length=10, max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        res = sb.table("keys").select("*").eq("key", key).gt("expires_at", now_iso).execute()
        if not res.data:
            await interaction.response.send_message("❌ Key not found or already expired.", ephemeral=True)
            return
        record = res.data[0]
        if record.get("hwid_reset_used"):
            await interaction.response.send_message(
                "❌ You've already used your one HWID reset for this key. Get a new key tomorrow.", ephemeral=True)
            return
        sb.table("keys").update({"hwid": None, "hwid_reset_used": True}).eq("key", key).execute()
        await interaction.response.send_message(
            f"✅ **HWID Reset!** Your key `{key}` can now be used on a new device.", ephemeral=True)


class TransferModal(discord.ui.Modal, title="Transfer Whitelist"):
    target_roblox_id = discord.ui.TextInput(
        label="New Owner's Roblox User ID",
        placeholder="Enter their Roblox User ID...",
        min_length=1, max_length=30,
    )

    def __init__(self, current_discord_tag: str):
        super().__init__()
        self.current_discord_tag = current_discord_tag

    async def on_submit(self, interaction: discord.Interaction):
        new_id = self.target_roblox_id.value.strip()
        res = sb.table("whitelist").select("*").eq("discord_tag", self.current_discord_tag).execute()
        if not res.data:
            await interaction.response.send_message("❌ You are not in the whitelist.", ephemeral=True)
            return
        old_id = res.data[0]["roblox_userid"]
        sb.table("whitelist").delete().eq("discord_tag", self.current_discord_tag).execute()
        sb.table("whitelist").insert({
            "roblox_userid": new_id,
            "discord_tag": f"Transferred from {self.current_discord_tag}",
            "note": f"Transferred from Roblox ID {old_id} by {self.current_discord_tag}",
        }).execute()
        await interaction.response.send_message(
            f"✅ Whitelist permanently transferred to Roblox ID `{new_id}`.\nYou have been removed from the whitelist.", ephemeral=True)


class TransferConfirmView(discord.ui.View):
    def __init__(self, discord_tag: str):
        super().__init__(timeout=60)
        self.discord_tag = discord_tag

    @discord.ui.button(label="⚠️ I Understand — Transfer Now", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferModal(self.discord_tag))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ Transfer cancelled.", view=None)


class ScriptView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Copy Script", style=discord.ButtonStyle.red, custom_id="larphub:copy_script")
    async def copy_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"**Paste this into your executor:**\n```lua\n{SCRIPT_LOADSTRING}\n```\n*Only you can see this.*",
            ephemeral=True)

    @discord.ui.button(label="🔄 Transfer Whitelist", style=discord.ButtonStyle.grey, custom_id="larphub:transfer_whitelist")
    async def transfer_whitelist(self, interaction: discord.Interaction, button: discord.ui.Button):
        tag = str(interaction.user)
        res = sb.table("whitelist").select("roblox_userid").eq("discord_tag", tag).execute()
        if not res.data:
            await interaction.response.send_message("❌ You are not whitelisted.", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚠️ Warning — This Action is Permanent",
            description=(
                "**Transferring your whitelist cannot be undone.**\n\n"
                "• You will be **removed from the whitelist** immediately.\n"
                "• The new user receives your premium access.\n"
                "• You must **re-purchase** to regain access.\n\n"
                "Are you absolutely sure?"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=TransferConfirmView(tag), ephemeral=True)

    @discord.ui.button(label="🖥️ Reset HWID", style=discord.ButtonStyle.grey, custom_id="larphub:reset_hwid")
    async def reset_hwid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResetHWIDModal())


# ── Discord Bot Commands ──────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(ctx):
    return any(r.id == ADMIN_ROLE_ID for r in ctx.author.roles)

@bot.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_event_loop()
    bot.add_view(ScriptView())
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="LarpHub"))
    print(f"Logged in as {bot.user} (id={bot.user.id})")


@bot.command()
async def script(ctx):
    if not is_admin(ctx): return await ctx.send("❌ No permission.")
    embed = discord.Embed(title="🎮 Larp Hub", color=discord.Color.from_rgb(188, 62, 62),
        description="Welcome to **Larp Hub** — the ultimate Roblox farming script.")
    embed.add_field(name="📜 How to Use", value=(
        "1. Click **📋 Copy Script** below.\n"
        "2. Open your Roblox executor.\n"
        "3. Paste and **Execute**.\n"
        "4. Enter your key when prompted."), inline=False)
    embed.add_field(name="🔑 Free Key", value=(
        f"1. Visit the [key checkpoint]({WORKINK_ENTRY_URL}).\n"
        "2. Complete the short tasks.\n"
        "3. Paste the key into Larp Hub.\n"
        "4. Keys last **24 hours**."), inline=False)
    embed.add_field(name="💎 Premium", value=(
        "Want **permanent access**? Create a **ticket** to purchase Premium.\n"
        "Premium users can **Transfer** their whitelist to another user *(permanent!)*"), inline=False)
    embed.add_field(name="🖥️ Changed Device?", value=
        "Use **Reset HWID** if you changed PC/executor and your key stopped working.", inline=False)
    embed.add_field(name="🔗 Referrals", value=
        "Ask an admin for your referral code. Every **3 friends** who use it = **48hr free key!**", inline=False)
    embed.set_footer(text="Larp Hub • Use responsibly")
    await ctx.send(embed=embed, view=ScriptView())
    try: await ctx.message.delete()
    except discord.Forbidden: pass


@bot.command()
async def whitelist(ctx, roblox_userid: str, *, note: str = ""):
    if not is_admin(ctx): return await ctx.send("No permission.")
    sb.table("whitelist").upsert({"roblox_userid": str(roblox_userid), "discord_tag": str(ctx.author), "note": note}).execute()
    await ctx.send(f"✅ Whitelisted `{roblox_userid}`.")

@bot.command()
async def unwhitelist(ctx, roblox_userid: str):
    if not is_admin(ctx): return await ctx.send("No permission.")
    sb.table("whitelist").delete().eq("roblox_userid", str(roblox_userid)).execute()
    await ctx.send(f"❌ Removed `{roblox_userid}` from whitelist.")

@bot.command()
async def genkey(ctx, hours: int = 24):
    if not is_admin(ctx): return await ctx.send("No permission.")
    new_key = generate_key_string()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    sb.table("keys").insert({"key": new_key, "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(), "roblox_userid": None,
        "hwid": None, "blacklisted": False, "hwid_reset_used": False}).execute()
    await ctx.send(f"🔑 Generated: `{new_key}` (Valid {hours}h)")

@bot.command()
async def blacklist(ctx, key: str):
    if not is_admin(ctx): return await ctx.send("No permission.")
    res = sb.table("keys").select("key").eq("key", key).execute()
    if not res.data: return await ctx.send(f"❌ Key `{key}` not found.")
    sb.table("keys").update({"blacklisted": True}).eq("key", key).execute()
    await ctx.send(f"🚫 Key `{key}` blacklisted.")

@bot.command()
async def unblacklist(ctx, key: str):
    if not is_admin(ctx): return await ctx.send("No permission.")
    res = sb.table("keys").select("key").eq("key", key).execute()
    if not res.data: return await ctx.send(f"❌ Key `{key}` not found.")
    sb.table("keys").update({"blacklisted": False}).eq("key", key).execute()
    await ctx.send(f"✅ Key `{key}` un-blacklisted.")

@bot.command()
async def keyinfo(ctx, key: str):
    if not is_admin(ctx): return await ctx.send("No permission.")
    res = sb.table("keys").select("*").eq("key", key).execute()
    if not res.data: return await ctx.send(f"❌ Key `{key}` not found.")
    r = res.data[0]
    expires = datetime.fromisoformat(r["expires_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    status = "🚫 Blacklisted" if r.get("blacklisted") else ("✅ Active" if expires > now else "❌ Expired")
    embed = discord.Embed(title=f"🔑 Key Info", color=discord.Color.from_rgb(188, 62, 62))
    embed.add_field(name="Key", value=f"`{key}`", inline=False)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Roblox User ID", value=r.get("roblox_userid") or "Not bound", inline=True)
    embed.add_field(name="HWID", value=r.get("hwid") or "Not set", inline=True)
    embed.add_field(name="IP Address", value=r.get("ip_address") or "N/A", inline=True)
    embed.add_field(name="Expires", value=f"<t:{int(expires.timestamp())}:R>", inline=True)
    embed.add_field(name="HWID Reset Used", value="Yes" if r.get("hwid_reset_used") else "No", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def whitelistcheck(ctx, roblox_userid: str):
    if not is_admin(ctx): return await ctx.send("No permission.")
    res = sb.table("whitelist").select("*").eq("roblox_userid", roblox_userid).execute()
    if not res.data:
        return await ctx.send(f"❌ Roblox ID `{roblox_userid}` is **not** whitelisted.")
    r = res.data[0]
    embed = discord.Embed(title="✅ Whitelisted", color=discord.Color.green())
    embed.add_field(name="Roblox User ID", value=r["roblox_userid"], inline=True)
    embed.add_field(name="Discord Tag", value=r.get("discord_tag") or "N/A", inline=True)
    embed.add_field(name="Note", value=r.get("note") or "None", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def stats(ctx):
    if not is_admin(ctx): return await ctx.send("No permission.")
    now_iso = datetime.now(timezone.utc).isoformat()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    active = sb.table("keys").select("key", count="exact").gt("expires_at", now_iso).eq("blacklisted", False).execute()
    today = sb.table("keys").select("key", count="exact").gte("created_at", today_start).execute()
    blisted = sb.table("keys").select("key", count="exact").eq("blacklisted", True).execute()
    wl = sb.table("whitelist").select("roblox_userid", count="exact").execute()
    refs = sb.table("referral_codes").select("code", count="exact").execute()
    embed = discord.Embed(title="📊 LarpHub Stats", color=discord.Color.from_rgb(188, 62, 62), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="🔑 Active Keys", value=str(active.count or 0), inline=True)
    embed.add_field(name="🗓️ Keys Today", value=str(today.count or 0), inline=True)
    embed.add_field(name="🚫 Blacklisted", value=str(blisted.count or 0), inline=True)
    embed.add_field(name="💎 Whitelisted", value=str(wl.count or 0), inline=True)
    embed.add_field(name="🔗 Referral Codes", value=str(refs.count or 0), inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def myreferral(ctx, roblox_userid: str):
    """Create or show a referral code for a Roblox user. Admin only."""
    if not is_admin(ctx): return await ctx.send("No permission.")
    res = sb.table("referral_codes").select("*").eq("owner_roblox_userid", roblox_userid).execute()
    if res.data:
        r = res.data[0]
        return await ctx.send(
            f"🔗 **Referral code for Roblox ID `{roblox_userid}`**\n"
            f"Code: `{r['code']}`\n"
            f"Uses: `{r['use_count']}`  |  Bonus keys given: `{r.get('bonus_keys_given', 0)}`")
    code = generate_referral_code()
    sb.table("referral_codes").insert({
        "code": code, "owner_roblox_userid": roblox_userid,
        "use_count": 0, "bonus_keys_given": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    await ctx.send(
        f"✅ **Referral code created for Roblox ID `{roblox_userid}`**\n"
        f"Code: `{code}`\n"
        f"Every **3 uses** = **48-hour bonus key** for the owner!")


# ── Entry Point ───────────────────────────────────────────────────────────────

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    bot.run(TOKEN)
