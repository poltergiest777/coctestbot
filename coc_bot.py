# coc_bot_cocpy.py
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import tasks, commands
from dotenv import load_dotenv
import coc

load_dotenv()
logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLAN_TAG = os.getenv("CLAN_TAG", "#2RQCG2CRV")
COC_API_KEY = os.getenv("COC_API_KEY")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID")) if os.getenv("ANNOUNCE_CHANNEL_ID") else None
REMINDER_OFFSET_MINUTES = int(os.getenv("REMINDER_OFFSET_MINUTES", 120))
CWl_POLL_MINUTES = int(os.getenv("CWl_POLL_MINUTES", 15))
WAR_POLL_MINUTES = int(os.getenv("WAR_POLL_MINUTES", 10))

# Bot setup
intents = discord.Intents.default()
intents.message_content = False
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# COC client
coc_client = coc.login(COC_API_KEY, is_async=True)

_last_reminder_war_end = None
_last_cwl_snapshot = None

# ---------------- Helpers ----------------
async def fetch_clan():
    return await coc_client.get_clan(CLAN_TAG)

async def fetch_current_war():
    clan = await fetch_clan()
    return await clan.war()

async def fetch_warlog():
    clan = await fetch_clan()
    return await clan.warlog()

async def fetch_cwl():
    clan = await fetch_clan()
    return await clan.current_war_league_group()

async def fetch_player(tag: str):
    return await coc_client.get_player(tag)

# ---------------- Formatters ----------------
def embed_from_clan(clan:coc.Clan) -> discord.Embed:
    embed = discord.Embed(title=f"{clan.name} — Roster", description=f"Tag: {clan.tag}", timestamp=datetime.now(timezone.utc))
    text = ""
    for m in clan.members[:25]:
        text += f"{m.name} — {m.role} — TH{m.town_hall}\n"
    embed.add_field(name="Top members", value=text or "No data", inline=False)
    return embed

def embed_from_player(player:coc.Player) -> discord.Embed:
    embed = discord.Embed(title=player.name, description=f"Tag: {player.tag}", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Town Hall", value=str(player.town_hall), inline=True)
    embed.add_field(name="Trophies", value=str(player.trophies), inline=True)
    embed.add_field(name="Best Trophies", value=str(player.best_trophies), inline=True)
    return embed

def embed_from_war(war:coc.ClanWar) -> discord.Embed:
    embed = discord.Embed(title="Current War", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="State", value=war.state, inline=True)
    embed.add_field(name="Team Size", value=war.team_size, inline=True)
    if war.opponent:
        embed.add_field(name="Opponent", value=war.opponent.name, inline=True)
    embed.add_field(name="Ends at (UTC)", value=war.end_time.isoformat(), inline=False)
    return embed

def compute_mvp_from_warlog_entry(war:coc.WarLogEntry):
    best = max(war.clan.members, key=lambda m: (m.stars, m.destruction))
    return best

# ---------------- Slash Commands ----------------
@tree.command(name="war", description="Show current war status", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def cmd_war(interaction: discord.Interaction):
    await interaction.response.defer()
    war = await fetch_current_war()
    await interaction.followup.send(embed=embed_from_war(war))

@tree.command(name="mvp", description="Show MVP of last war", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def cmd_mvp(interaction: discord.Interaction):
    await interaction.response.defer()
    warlog = await fetch_warlog()
    if not warlog:
        await interaction.followup.send("No warlog found.")
        return
    last = warlog[0]
    best = compute_mvp_from_warlog_entry(last)
    embed = discord.Embed(title="MVP — Last War", description=f"Opponent: {last.opponent.name if last.opponent else '?'}", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Player", value=best.name, inline=True)
    embed.add_field(name="Stars", value=str(best.stars), inline=True)
    embed.add_field(name="Destruction %", value=str(best.destruction), inline=True)
    await interaction.followup.send(embed=embed)

@tree.command(name="cwl", description="Show CWL status", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def cmd_cwl(interaction: discord.Interaction):
    await interaction.response.defer()
    cwl = await fetch_cwl()
    embed = discord.Embed(title="CWL Info", timestamp=datetime.now(timezone.utc))
    embed.description = cwl.state
    embed.add_field(name="Rounds", value=len(cwl.rounds) if cwl.rounds else 0)
    await interaction.followup.send(embed=embed)

@tree.command(name="roster", description="Show clan roster", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def cmd_roster(interaction: discord.Interaction):
    await interaction.response.defer()
    clan = await fetch_clan()
    await interaction.followup.send(embed=embed_from_clan(clan))

@tree.command(name="player", description="Get player stats by tag (include #)", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.describe(tag="Player tag, include #")
async def cmd_player(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()
    player = await fetch_player(tag)
    await interaction.followup.send(embed=embed_from_player(player))

# ---------------- Background Tasks ----------------
@tasks.loop(minutes=CWL_POLL_MINUTES)
async def cwl_loop():
    global _last_cwl_snapshot
    if not ANNOUNCE_CHANNEL_ID:
        return
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return
    cwl = await fetch_cwl()
    key = str(cwl)
    if _last_cwl_snapshot != key:
        _last_cwl_snapshot = key
        embed = discord.Embed(title="CWL Update", description=cwl.state, timestamp=datetime.now(timezone.utc))
        await channel.send(embed=embed)

@tasks.loop(minutes=WAR_POLL_MINUTES)
async def war_reminder_loop():
    global _last_reminder_war_end
    if not ANNOUNCE_CHANNEL_ID:
        return
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return
    war = await fetch_current_war()
    if not war:
        return
    end_dt = war.end_time
    if _last_reminder_war_end == end_dt:
        return
    now = datetime.now(timezone.utc)
    minutes_left = (end_dt - now).total_seconds()/60
    if 0 < minutes_left <= REMINDER_OFFSET_MINUTES:
        await channel.send(f"🔔 War ends in about **{int(minutes_left)} minutes** — finish your final attacks!")
        _last_reminder_war_end = end_dt

# ---------------- Startup ----------------
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (id: {bot.user.id})")
    if GUILD_ID:
        await tree.sync(guild=discord.Object(id=GUILD_ID))
    else:
        await tree.sync()
    cwl_loop.start()
    war_reminder_loop.start()
    if ANNOUNCE_CHANNEL_ID:
        ch = bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if ch:
            await ch.send("Clan bot online ✅")

if __name__ == "__main__":
    if not DISCORD_TOKEN or not COC_API_KEY:
        raise SystemExit("Set DISCORD_TOKEN and COC_API_KEY in .env")
    bot.run(DISCORD_TOKEN)
