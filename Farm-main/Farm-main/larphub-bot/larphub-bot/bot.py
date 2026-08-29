import os
import secrets
import string
import threading
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, make_response, render_template_string
import discord
from discord.ext import commands
from supabase import create_client

TOKEN = os.environ["DISCORD_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ADMIN_ROLE_ID = int(os.environ["ADMIN_ROLE_ID"])
WORKINK_API_KEY = os.environ["WORKINK_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))
WORKINK_ENTRY_URL = os.environ.get("WORKINK_ENTRY_URL", "https://work.ink/2U7C/larp-hub-key")
SCRIPT_LOADSTRING = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/yourabum23/LarpHub/refs/heads/main/Farm-main/Farm-main/larphub-bot/larphub-bot/LarpHub.lua"))()'

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

    if resp.status_code == 401:
        return False, "bad_api_key"
    if resp.status_code == 403:
        return False, "wrong_account"
    if resp.status_code != 200:
        return False, "unexpected_status"

    try:
        data = resp.json()
    except ValueError:
        return False, "bad_json"

    if not data.get("valid"):
        return False, "invalid_or_used"

    info = data.get("info") or {}
    token_ip = info.get("byIp")
    if token_ip and len(token_ip) <= 45 and token_ip != client_ip:
        return False, "ip_mismatch"

    return True, "ok"

KEY_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Larp Hub - 24-Hour Key</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #120808; color: #ffffff; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background-color: #1f0d0d; border: 2px solid #bc3e3e; border-radius: 16px; padding: 36px 30px; max-width: 440px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(188,62,62,0.25); }
        h1 { color: #bc3e3e; font-size: 28px; margin-bottom: 8px; }
        .tag { display: inline-block; background: #381212; color: #ff8585; font-size: 12px; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px; font-weight: bold; }
        p { color: #d6b4b4; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .key-container { background-color: #2b1111; border: 1px dashed #bc3e3e; border-radius: 10px; padding: 16px; font-size: 20px; font-weight: bold; color: #ffffff; letter-spacing: 2px; margin-bottom: 20px; user-select: all; word-break: break-all; }
        .btn { background-color: #bc3e3e; color: #ffffff; border: none; border-radius: 8px; padding: 12px 24px; font-size: 15px; font-weight: bold; cursor: pointer; width: 100%; transition: 0.2s ease; }
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
            var text = document.getElementById("keyText").innerText.trim();
            navigator.clipboard.writeText(text).then(function() {
                var btn = document.querySelector(".btn");
                btn.innerText = "Copied to Clipboard!";
                btn.style.backgroundColor = "#2e8b57";
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
        body { background-color: #120808; color: #ffffff; display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px; }
        .card { background-color: #1f0d0d; border: 2px solid #bc3e3e; border-radius: 16px; padding: 36px 30px; max-width: 440px; width: 100%; text-align: center; }
        h1 { color: #bc3e3e; font-size: 24px; margin-bottom: 16px; }
        p { color: #d6b4b4; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .btn { display: inline-block; background-color: #bc3e3e; color: #ffffff; text-decoration: none; border: none; border-radius: 8px; padding: 12px 24px; font-size: 15px; font-weight: bold; }
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

@app.route("/getkey", methods=["GET"])
def get_key_page():
    now_iso = datetime.now(timezone.utc).isoformat()
    client_ip = get_client_ip()
    cookie_key = request.cookies.get("larp_active_key")
    token = request.args.get("token")
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
        response = make_response(render_template_string(
            KEY_PAGE_HTML,
            key=key,
            tag_text=f"ACTIVE KEY ({hours}h {minutes}m remaining)",
            message_text="You already have an active 24-hour key. You don't need to do any more checkpoints until this key expires!"
        ))
        response.set_cookie("larp_active_key", key, max_age=int(remaining.total_seconds()), httponly=True, samesite="Lax")
        return response

    if not token:
        return render_template_string(
            ERROR_PAGE_HTML,
            heading="Checkpoint Required",
            message_text="You need to complete the checkpoint before you can get a key.",
            workink_url=WORKINK_ENTRY_URL,
        ), 400

    ok, reason = verify_workink_token(token, client_ip)

    if not ok:
        if reason == "invalid_or_used":
            msg = "This checkpoint link is invalid, expired, or has already been redeemed. Please complete a new checkpoint."
        elif reason == "ip_mismatch":
            msg = "This checkpoint was completed from a different network. You must complete the checkpoint yourself."
        elif reason in ("bad_api_key", "wrong_account"):
            app.logger.error(f"Work.ink verification config error: {reason}")
            msg = "Something's misconfigured on our end. Please try again shortly or contact staff."
        else:
            msg = "We couldn't verify that checkpoint. Please complete it again."
        return render_template_string(
            ERROR_PAGE_HTML,
            heading="Checkpoint Verification Failed",
            message_text=msg,
            workink_url=WORKINK_ENTRY_URL,
        ), 400

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

class TransferModal(discord.ui.Modal, title="Transfer Whitelist"):
    target_roblox_id = discord.ui.TextInput(
        label="New Owner's Roblox User ID",
        placeholder="Enter the Roblox User ID of the person receiving your whitelist...",
        min_length=1,
        max_length=30
    )

    def __init__(self, current_discord_tag: str):
        super().__init__()
        self.current_discord_tag = current_discord_tag

    async def on_submit(self, interaction: discord.Interaction):
        new_roblox_id = self.target_roblox_id.value.strip()

        res = sb.table("whitelist").select("*").eq("discord_tag", self.current_discord_tag).execute()
        if not res.data:
            await interaction.response.send_message(
                "❌ You are not in the whitelist. You can't transfer what you don't have.",
                ephemeral=True
            )
            return

        entry = res.data[0]
        old_roblox_id = entry["roblox_userid"]

        sb.table("whitelist").delete().eq("discord_tag", self.current_discord_tag).execute()
        sb.table("whitelist").insert({
            "roblox_userid": new_roblox_id,
            "discord_tag": f"Transferred from {self.current_discord_tag}",
            "note": f"Transferred from Roblox ID {old_roblox_id} by {self.current_discord_tag}"
        }).execute()

        await interaction.response.send_message(
            f"✅ Your whitelist has been permanently transferred to Roblox User ID `{new_roblox_id}`.\n"
            f"You have been removed from the whitelist. You will need to re-purchase to regain access.",
            ephemeral=True
        )


class TransferConfirmView(discord.ui.View):
    def __init__(self, discord_tag: str):
        super().__init__(timeout=60)
        self.discord_tag = discord_tag

    @discord.ui.button(label="⚠️ I Understand — Transfer Now", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferModal(self.discord_tag))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ Transfer cancelled. Your whitelist is safe.", view=None)


class ScriptView(discord.ui.View):
    """Persistent view with Copy Script and Transfer buttons."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Copy Script",
        style=discord.ButtonStyle.red,
        custom_id="larphub:copy_script"
    )
    async def copy_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"**Copy the script below and paste it into your executor:**\n"
            f"```lua\n{SCRIPT_LOADSTRING}\n```\n"
            f"*Only you can see this message.*",
            ephemeral=True
        )

    @discord.ui.button(
        label="🔄 Transfer Whitelist",
        style=discord.ButtonStyle.grey,
        custom_id="larphub:transfer_whitelist"
    )
    async def transfer_whitelist(self, interaction: discord.Interaction, button: discord.ui.Button):
        discord_tag = str(interaction.user)

        res = sb.table("whitelist").select("roblox_userid").eq("discord_tag", discord_tag).execute()
        if not res.data:
            await interaction.response.send_message(
                "❌ You are not whitelisted. Only premium users with an active whitelist can transfer.",
                ephemeral=True
            )
            return

        warning_embed = discord.Embed(
            title="⚠️ Warning — This Action is Permanent",
            description=(
                "**Transferring your whitelist cannot be undone.**\n\n"
                "• You will be **removed from the whitelist** immediately.\n"
                "• The new user will receive your premium access.\n"
                "• You will need to **re-purchase** if you want access again.\n\n"
                "Are you absolutely sure you want to continue?"
            ),
            color=discord.Color.orange()
        )

        await interaction.response.send_message(
            embed=warning_embed,
            view=TransferConfirmView(discord_tag),
            ephemeral=True
        )

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def is_admin(ctx):
    return any(r.id == ADMIN_ROLE_ID for r in ctx.author.roles)


@bot.event
async def on_ready():

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="LarpHub")
    )

    bot.add_view(ScriptView())
    print(f"Logged in as {bot.user} (id={bot.user.id})")


@bot.command()
async def script(ctx):
    """Posts the LarpHub info embed with Copy Script and Transfer buttons. Admin only."""
    if not is_admin(ctx):
        return await ctx.send("❌ No permission.")

    embed = discord.Embed(
        title="<:larphub:> Larp Hub",
        description=(
            "Welcome to **Larp Hub** — the ultimate Roblox farming script.\n"
            "Use the buttons below to get the script or manage your whitelist."
        ),
        color=discord.Color.from_rgb(188, 62, 62)
    )

    embed.add_field(
        name="📜 How to Use the Script",
        value=(
            "1. Click **📋 Copy Script** below.\n"
            "2. Open your Roblox executor (e.g. Synapse, KRNL, Delta).\n"
            "3. Paste the script and hit **Execute**.\n"
            "4. Enter your key when prompted."
        ),
        inline=False
    )

    embed.add_field(
        name="🔑 How to Get a Free Key",
        value=(
            f"1. Visit the [key checkpoint]({WORKINK_ENTRY_URL}).\n"
            "2. Complete the short tasks on the page.\n"
            "3. Your key will be shown — copy and paste it into Larp Hub.\n"
            "4. Keys last **24 hours**. Come back tomorrow for a new one."
        ),
        inline=False
    )

    embed.add_field(
        name="💎 Premium / Whitelist",
        value=(
            "Want **permanent access** with no daily key needed?\n"
            "Create a **ticket** in this server to purchase Premium.\n"
            "Premium users can also use the **🔄 Transfer** button to give their "
            "whitelist to another user *(this is permanent and cannot be undone)*."
        ),
        inline=False
    )

    embed.set_footer(text="Larp Hub • Use responsibly")
    embed.set_thumbnail(url="https://i.imgur.com/your_logo_here.png")

    await ctx.send(embed=embed, view=ScriptView())

    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass


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
