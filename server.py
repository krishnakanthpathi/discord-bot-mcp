"""
Discord Bot MCP Server.
Provides full Discord administration, messaging, file attachments,
and voice channel audio streaming via FastMCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from voice_controller import VoiceController

# Load environment configuration
load_dotenv(Path(__file__).parent / ".env")
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DEFAULT_GUILD_ID = os.getenv("DISCORD_DEFAULT_GUILD_ID", "").strip()

# Setup logging to stderr ONLY (keep stdout clean for JSON-RPC transport)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("discord-bot-mcp")

# Initialize Discord client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.voice_states = True

bot = discord.Client(intents=intents)
voice = VoiceController(bot)

# Initialize MCP Server
mcp = MCPServer("discord-bot-mcp")


# ---------------------------------------------------------
# Discord Gateway Event Handlers
# ---------------------------------------------------------

@bot.event
async def on_ready():
    logger.info("Discord Bot connected as %s (ID: %s)", bot.user, bot.user.id if bot.user else "Unknown")
    for guild in bot.guilds:
        logger.info("Connected to Guild: %s (ID: %s)", guild.name, guild.id)


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error("Unhandled Discord event error in %s", event, exc_info=True)




# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------

def _resolve_guild_id(guild_id: Optional[str] = None) -> int:
    gid = guild_id or DEFAULT_GUILD_ID
    if not gid:
        if bot.guilds:
            return bot.guilds[0].id
        raise ValueError("No guild_id provided and no connected guilds found.")
    return int(gid)


# ---------------------------------------------------------
# MCP Tools: Identity & Discovery
# ---------------------------------------------------------

@mcp.tool()
async def discord_whoami() -> Dict[str, Any]:
    """Returns the bot's username, ID, connection status, and list of servers."""
    if not bot.is_ready():
        return {"status": "connecting", "user": None, "guilds": []}

    return {
        "status": "ready",
        "username": str(bot.user),
        "id": str(bot.user.id),
        "avatar_url": str(bot.user.avatar.url) if bot.user.avatar else None,
        "guilds": [{"id": str(g.id), "name": g.name, "member_count": g.member_count} for g in bot.guilds],
    }


@mcp.tool()
async def discord_list_guilds() -> List[Dict[str, Any]]:
    """Lists all Discord servers (guilds) the bot has access to."""
    return [
        {
            "id": str(g.id),
            "name": g.name,
            "member_count": g.member_count,
            "owner_id": str(g.owner_id),
        }
        for g in bot.guilds
    ]


# ---------------------------------------------------------
# MCP Tools: Channel & Category Management
# ---------------------------------------------------------

@mcp.tool()
async def discord_list_channels(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Lists all channels in a guild grouped by categories."""
    gid = _resolve_guild_id(guild_id)
    guild = bot.get_guild(gid)
    if not guild:
        return {"error": f"Guild {gid} not found"}

    categories = []
    uncategorized = []

    for cat in guild.categories:
        cat_channels = []
        for ch in cat.channels:
            cat_channels.append({
                "id": str(ch.id),
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
            })
        categories.append({
            "id": str(cat.id),
            "name": cat.name,
            "position": cat.position,
            "channels": cat_channels,
        })

    for ch in guild.channels:
        if ch.category is None and not isinstance(ch, discord.CategoryChannel):
            uncategorized.append({
                "id": str(ch.id),
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
            })

    return {
        "guild_id": str(guild.id),
        "guild_name": guild.name,
        "categories": categories,
        "uncategorized": uncategorized,
    }


@mcp.tool()
async def discord_create_text_channel(
    name: str,
    guild_id: Optional[str] = None,
    category_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a new text channel in the guild."""
    gid = _resolve_guild_id(guild_id)
    guild = bot.get_guild(gid)
    if not guild:
        return {"error": f"Guild {gid} not found"}

    try:
        category = guild.get_channel(int(category_id)) if category_id else None
        new_ch = await guild.create_text_channel(name=name, category=category, topic=topic)
        return {
            "success": True,
            "id": str(new_ch.id),
            "name": new_ch.name,
            "guild_id": str(guild.id),
        }
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Create text channel error: %s", err_msg)
        return {"error": err_msg}


@mcp.tool()
async def discord_create_voice_channel(
    name: str,
    guild_id: Optional[str] = None,
    category_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a new voice channel in the guild."""
    gid = _resolve_guild_id(guild_id)
    guild = bot.get_guild(gid)
    if not guild:
        return {"error": f"Guild {gid} not found"}

    try:
        category = guild.get_channel(int(category_id)) if category_id else None
        new_vc = await guild.create_voice_channel(name=name, category=category)
        return {
            "success": True,
            "id": str(new_vc.id),
            "name": new_vc.name,
            "guild_id": str(guild.id),
        }
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Create voice channel error: %s", err_msg)
        return {"error": err_msg}


@mcp.tool()
async def discord_delete_channel(channel_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Deletes a channel by ID."""
    ch = bot.get_channel(int(channel_id))
    if not ch:
        return {"error": f"Channel {channel_id} not found"}

    try:
        name = ch.name
        await ch.delete(reason=reason)
        return {"success": True, "deleted_channel_id": channel_id, "name": name}
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Delete channel error: %s", err_msg)
        return {"error": err_msg}


@mcp.tool()
async def discord_create_category(name: str, guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Creates a new channel category in the guild."""
    gid = _resolve_guild_id(guild_id)
    guild = bot.get_guild(gid)
    if not guild:
        return {"error": f"Guild {gid} not found"}

    try:
        cat = await guild.create_category(name=name)
        return {"success": True, "id": str(cat.id), "name": cat.name}
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Create category error: %s", err_msg)
        return {"error": err_msg}


@mcp.tool()
async def discord_delete_category(category_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Deletes a channel category by ID."""
    ch = bot.get_channel(int(category_id))
    if not ch or not isinstance(ch, discord.CategoryChannel):
        return {"error": f"Category {category_id} not found"}

    try:
        name = ch.name
        await ch.delete(reason=reason)
        return {"success": True, "deleted_category_id": category_id, "name": name}
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Delete category error: %s", err_msg)
        return {"error": err_msg}


# ---------------------------------------------------------
# MCP Tools: Messaging & Attachments
# ---------------------------------------------------------

@mcp.tool()
async def discord_send_message(
    channel_id: str,
    content: str = "",
    embed_title: Optional[str] = None,
    embed_description: Optional[str] = None,
    embed_color: int = 0x5865F2,
    file_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Sends a message to a Discord channel with optional embed and file attachments.
    file_paths: Local filesystem paths to attach (e.g. ['/path/to/doc.pdf', '/path/to/image.png']).
    """
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return {"error": f"Channel {channel_id} not found"}

    embed = None
    if embed_title or embed_description:
        embed = discord.Embed(
            title=embed_title,
            description=embed_description,
            color=embed_color,
        )

    files_to_send = []
    if file_paths:
        for p in file_paths:
            path_obj = Path(p).expanduser().resolve()
            if not path_obj.exists():
                return {"error": f"Attachment file not found: {p}"}
            files_to_send.append(discord.File(str(path_obj)))

    msg = await channel.send(
        content=content if content else None,
        embed=embed,
        files=files_to_send if files_to_send else None,
    )

    return {
        "success": True,
        "message_id": str(msg.id),
        "channel_id": str(channel.id),
        "attachments_count": len(msg.attachments),
    }


@mcp.tool()
async def discord_create_thread(
    name: str,
    channel_id: Optional[str] = None,
    message_id: Optional[str] = None,
    auto_archive_duration: int = 1440,
) -> Dict[str, Any]:
    """
    Creates a new public thread either attached to an existing message or directly in a channel.
    """
    try:
        if message_id and channel_id:
            ch = bot.get_channel(int(channel_id))
            if not ch:
                return {"error": f"Channel {channel_id} not found"}
            msg = await ch.fetch_message(int(message_id))
            thread = await msg.create_thread(name=name, auto_archive_duration=auto_archive_duration)
        elif channel_id:
            ch = bot.get_channel(int(channel_id))
            if not ch or not hasattr(ch, "create_thread"):
                return {"error": f"Channel {channel_id} cannot create threads"}
            thread = await ch.create_thread(name=name, auto_archive_duration=auto_archive_duration)
        else:
            return {"error": "Either channel_id or both channel_id and message_id must be provided"}

        return {
            "success": True,
            "id": str(thread.id),
            "name": thread.name,
            "parent_id": str(thread.parent_id),
        }
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Create thread error: %s", err_msg)
        return {"error": err_msg}


@mcp.tool()
async def discord_read_messages(channel_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Reads recent messages from a channel."""
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return [{"error": f"Channel {channel_id} not found"}]

    messages = []
    async for msg in channel.history(limit=limit):
        messages.append({
            "id": str(msg.id),
            "author": str(msg.author),
            "author_id": str(msg.author.id),
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
            "attachments": [att.url for att in msg.attachments],
            "reactions": [{"emoji": str(r.emoji), "count": r.count} for r in msg.reactions],
        })
    return messages


@mcp.tool()
async def discord_add_reaction(channel_id: str, message_id: str, emoji: str) -> Dict[str, Any]:
    """Adds an emoji reaction to a specific message."""
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return {"error": f"Channel {channel_id} not found"}

    msg = await channel.fetch_message(int(message_id))
    await msg.add_reaction(emoji)
    return {"success": True, "message_id": message_id, "emoji": emoji}



# ---------------------------------------------------------
# MCP Tools: Voice & Audio Streaming
# ---------------------------------------------------------

@mcp.tool()
async def discord_join_voice(channel_id: str) -> Dict[str, Any]:
    """Connects the bot to the specified voice channel."""
    ch = bot.get_channel(int(channel_id))
    if not ch or not isinstance(ch, discord.VoiceChannel):
        return {"error": f"Voice channel {channel_id} not found"}

    vc = await voice.join_channel(ch)
    return {
        "success": True,
        "channel_id": str(ch.id),
        "channel_name": ch.name,
        "guild_id": str(ch.guild.id),
    }


@mcp.tool()
async def discord_leave_voice(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Disconnects the bot from the guild voice channel."""
    gid = _resolve_guild_id(guild_id)
    disconnected = await voice.leave_channel(gid)
    return {"success": disconnected, "guild_id": str(gid)}


@mcp.tool()
async def discord_play_audio(
    channel_id: str,
    audio_source: str,
    volume: float = 1.0,
) -> Dict[str, Any]:
    """
    Streams audio into the specified Discord voice channel.
    `channel_id`: The ID of the Discord voice channel (e.g. from `discord_list_channels`).
    `audio_source`: Can be a YouTube URL (e.g. 'https://www.youtube.com/watch?v=...'), a local audio file path (e.g. '/tmp/stefan_theme.mp3'), or a direct stream URL.
    """
    ch = bot.get_channel(int(channel_id))
    if not ch or not isinstance(ch, discord.VoiceChannel):
        return {"error": f"Voice channel {channel_id} not found"}

    try:
        res = await voice.play_audio(ch, audio_source=audio_source, volume=volume)
        return {"success": True, **res}
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Audio playback error: %s", err_msg, exc_info=True)
        return {"error": err_msg}


@mcp.tool()
async def discord_stop_audio(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Stops currently playing audio in the guild voice channel."""
    gid = _resolve_guild_id(guild_id)
    stopped = voice.stop_audio(gid)
    return {"success": stopped, "guild_id": str(gid)}


@mcp.tool()
async def discord_voice_status(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Returns the current voice connection and playback status."""
    gid = _resolve_guild_id(guild_id)
    return voice.get_status(gid)


# ---------------------------------------------------------
# Main Server Runner
# ---------------------------------------------------------

async def main():
    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN environment variable is missing!")
        sys.exit(1)

    logger.info("Starting Discord Bot background connection...")
    # Launch Discord gateway in the background
    asyncio.create_task(bot.start(BOT_TOKEN))

    # Wait for bot to be ready before accepting MCP traffic
    timeout = 30
    while not bot.is_ready() and timeout > 0:
        await asyncio.sleep(0.5)
        timeout -= 0.5

    if bot.is_ready():
        logger.info("Discord Bot is fully connected and ready.")
    else:
        logger.warning("Discord Bot took longer than 15s to connect; starting MCP server anyway.")

    logger.info("Starting MCP stdio server transport...")
    await mcp.run_stdio_async()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Discord Bot MCP Server shutting down.")
