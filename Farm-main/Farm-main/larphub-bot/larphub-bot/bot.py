import os
import threading
from flask import Flask
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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")


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
        await ctx.send(f"Whitelisted `{roblox_userid}`")
    else:
        await ctx.send("Failed to write to Supabase.")


@bot.command()
async def unwhitelist(ctx, roblox_userid: str):
    """!unwhitelist 123456789"""
    if not is_admin(ctx):
        return await ctx.send("No permission.")

    result = (
        sb.table("whitelist")
        .delete()
        .eq("roblox_userid", str(roblox_userid))
        .execute()
    )

    if result.data:
        await ctx.send(f"Removed `{roblox_userid}`")
    else:
        await ctx.send(f"`{roblox_userid}` was not on the whitelist.")


@bot.command()
async def whitelistcheck(ctx, roblox_userid: str):
    """!whitelistcheck 123456789"""
    result = (
        sb.table("whitelist")
        .select("*")
        .eq("roblox_userid", str(roblox_userid))
        .execute()
    )

    if result.data:
        row = result.data[0]
        await ctx.send(
            f"`{roblox_userid}` **is** whitelisted\n"
            f"Note: {row.get('note') or '-'}\n"
            f"Added by: {row.get('discord_tag') or '-'}"
        )
    else:
        await ctx.send(f"`{roblox_userid}` is **not** whitelisted.")


@bot.command()
async def whitelistlist(ctx):
    """!whitelistlist"""
    if not is_admin(ctx):
        return await ctx.send("No permission.")

    result = (
        sb.table("whitelist")
        .select("roblox_userid, note, discord_tag")
        .limit(50)
        .execute()
    )

    if not result.data:
        return await ctx.send("Whitelist is empty.")

    lines = [
        f"`{r['roblox_userid']}` — {r.get('note') or ''} ({r.get('discord_tag') or ''})"
        for r in result.data
    ]
    await ctx.send("**Whitelist:**\n" + "\n".join(lines))


# Keep-alive HTTP server so Render free web service stays up
app = Flask(__name__)


@app.get("/")
def home():
    return "LarpHub bot online", 200


@app.get("/health")
def health():
    return {"ok": True, "bot": str(bot.user) if bot.user else None}, 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
