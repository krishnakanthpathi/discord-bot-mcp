"""
Discord Bot MCP Server & AI Agent Gateway.
Provides Discord administration, messaging, file attachments,
voice channel audio streaming, and an AI Agent server mode.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set

import discord
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from agent_bridge import (
    DEFAULT_DISCORD_SYSTEM_PROMPT,
    AgentBridge,
    split_discord_message,
)
from voice_controller import VoiceController

# Load environment configuration
load_dotenv(Path(__file__).parent / ".env")

# Setup logging to stderr ONLY (keeps stdout clean for MCP JSON-RPC transport)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("discord-bot-mcp")


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

def _parse_user_ids(raw: Optional[str]) -> Set[str]:
    """Parses comma-separated Discord user IDs into a set of strings."""
    if not raw:
        return set()
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


@dataclass
class ServerConfig:
    bot_token: str = field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", "").strip())
    default_guild_id: str = field(default_factory=lambda: os.getenv("DISCORD_DEFAULT_GUILD_ID", "").strip())
    allowed_users: Set[str] = field(default_factory=lambda: _parse_user_ids(os.getenv("DISCORD_ALLOWED_USERS", "")))
    agent_backend: str = field(default_factory=lambda: os.getenv("DISCORD_AGENT_BACKEND", "antigravity").strip().lower())
    agent_bin: Optional[str] = field(default_factory=lambda: os.getenv("DISCORD_AGENT_BIN", None))
    agent_model: Optional[str] = field(default_factory=lambda: os.getenv("DISCORD_AGENT_MODEL", None))
    system_prompt: Optional[str] = field(default_factory=lambda: os.getenv("DISCORD_SYSTEM_PROMPT", None))
    custom_cmd: Optional[str] = field(default_factory=lambda: os.getenv("DISCORD_AGENT_CUSTOM_CMD", None))
    allow_dms: bool = True
    allow_mentions: bool = True
    agent_mode: bool = False
    silent_reject: bool = field(default_factory=lambda: os.getenv("DISCORD_SILENT_REJECT", "false").lower() == "true")


CONFIG = ServerConfig()
AGENT: Optional[AgentBridge] = None

# Initialize Discord client with required intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.dm_messages = True
intents.guild_messages = True
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
    if CONFIG.allowed_users:
        logger.info("Security Whitelist: %d allowed user ID(s) active (%s)", len(CONFIG.allowed_users), ", ".join(sorted(CONFIG.allowed_users)))
    else:
        logger.warning("Security Whitelist: No allowed users configured! Bot will ignore all incoming agent prompts.")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error("Unhandled Discord event error in %s", event, exc_info=True)


@bot.event
async def on_message(message: discord.Message):
    # Ignore messages sent by bots (including ourself)
    if message.author.bot or message.author == bot.user:
        return

    # Check if agent responder is active
    if not CONFIG.agent_mode:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions if bot.user else False

    # Check trigger condition (Direct Message or @Mention)
    if not ((is_dm and CONFIG.allow_dms) or (is_mention and CONFIG.allow_mentions)):
        return

    author_id = str(message.author.id)
    user_display = f"{message.author} (ID: {author_id})"

    # Whitelist authorization verification
    if CONFIG.allowed_users and author_id not in CONFIG.allowed_users:
        logger.warning("[SECURITY] Rejected message from unauthorized user %s", user_display)
        if not CONFIG.silent_reject:
            denied_msg = (
                f"⛔ **Access Denied**: You are not authorized to use this AI agent.\n"
                f"Your Discord User ID is: `{author_id}`\n"
                f"Ask the server administrator to add your ID to the allowed list (`--allowed-users` or `DISCORD_ALLOWED_USERS` in `.env`)."
            )
            try:
                await message.reply(denied_msg, mention_author=False)
            except Exception as e:
                logger.error("Failed to send unauthorized notification: %s", e)
        return

    # Extract and clean the prompt content
    raw_content = message.content or ""
    if bot.user:
        raw_content = re.sub(rf"<@!?{bot.user.id}>", "", raw_content)
    clean_prompt = raw_content.strip()

    if not clean_prompt:
        await message.reply(
            "👋 **Hello!** I am your connected AI Agent. Send me a question, task, or request and I'll respond right here!",
            mention_author=False,
        )
        return

    logger.info("Processing prompt from %s in %s: %s", user_display, message.channel, clean_prompt[:80])

    # Show Discord typing indicator while generating AI response
    try:
        async with message.channel.typing():
            global AGENT
            if AGENT is None:
                AGENT = AgentBridge(
                    backend=CONFIG.agent_backend,
                    agent_bin=CONFIG.agent_bin,
                    model=CONFIG.agent_model,
                    system_prompt=CONFIG.system_prompt,
                    custom_cmd=CONFIG.custom_cmd,
                )

            reply_text = await AGENT.execute(clean_prompt, user_name=str(message.author))
            raw_chunks = split_discord_message(reply_text)
            chunks = [c.strip() for c in raw_chunks if c and c.strip()]
            if not chunks:
                chunks = ["🤖 *(The agent processed your message, but the reply was blank. Please try asking again!)*"]

            # Reply with the first chunk
            await message.reply(chunks[0], mention_author=False)

            # Send any subsequent chunks sequentially
            for chunk in chunks[1:]:
                if chunk and chunk.strip():
                    await message.channel.send(chunk.strip())

            logger.info("Successfully delivered response (%d chunk(s)) to %s", len(chunks), user_display)

    except Exception as exc:
        logger.error("Agent execution failed for user %s: %s", user_display, exc, exc_info=True)
        try:
            err_msg = f"❌ **Agent Error**: {str(exc)}"
            if not err_msg.strip():
                err_msg = "❌ **Agent Error**: An unexpected internal error occurred."
            await message.reply(err_msg, mention_author=False)
        except Exception:
            pass


# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------

def _resolve_guild_id(guild_id: Optional[str] = None) -> int:
    gid = guild_id or CONFIG.default_guild_id
    if not gid:
        if bot.guilds:
            return bot.guilds[0].id
        raise ValueError("No guild_id provided and no connected guilds found.")
    return int(gid)


# ---------------------------------------------------------
# MCP Tools: Identity & Status
# ---------------------------------------------------------

@mcp.tool()
async def discord_status() -> Dict[str, Any]:
    """
    Returns the bot's connection status, validity, latency, user profile, and guild list.
    Verifies whether the bot is validly connected to Discord.
    """
    if not bot.is_ready():
        return {
            "valid": False,
            "status": "connecting" if CONFIG.bot_token else "invalid_token",
            "message": "Discord bot is not connected or token is missing/invalid.",
            "bot_user": None,
            "guilds": [],
            "allowed_users_count": len(CONFIG.allowed_users),
            "agent_backend": CONFIG.agent_backend,
        }

    return {
        "valid": True,
        "status": "ready",
        "username": str(bot.user),
        "id": str(bot.user.id),
        "avatar_url": str(bot.user.avatar.url) if bot.user.avatar else None,
        "latency_ms": round(bot.latency * 1000, 2) if bot.latency else 0.0,
        "guilds_count": len(bot.guilds),
        "guilds": [
            {"id": str(g.id), "name": g.name, "member_count": g.member_count}
            for g in bot.guilds
        ],
        "allowed_users_count": len(CONFIG.allowed_users),
        "allowed_users": list(sorted(CONFIG.allowed_users)),
        "agent_backend": CONFIG.agent_backend,
        "agent_mode_active": CONFIG.agent_mode,
    }


@mcp.tool()
async def discord_whoami() -> Dict[str, Any]:
    """
    Backwards-compatible alias for discord_status.
    Returns the bot's username, ID, connection status, and list of servers.
    """
    return await discord_status()


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
async def discord_send_direct_message(
    user_id: str,
    content: str = "",
    embed_title: Optional[str] = None,
    embed_description: Optional[str] = None,
    embed_color: int = 0x5865F2,
    file_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Sends a direct message (DM) to a specific Discord user by their User ID.
    `user_id`: Numeric Discord Snowflake User ID.
    """
    try:
        uid = int(user_id)
        user = bot.get_user(uid)
        if not user:
            user = await bot.fetch_user(uid)
        if not user:
            return {"error": f"User {user_id} not found"}

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

        msg = await user.send(
            content=content if content else None,
            embed=embed,
            files=files_to_send if files_to_send else None,
        )

        return {
            "success": True,
            "message_id": str(msg.id),
            "recipient_id": str(user.id),
            "recipient_username": str(user),
            "dm_channel_id": str(msg.channel.id),
            "attachments_count": len(msg.attachments),
        }
    except Exception as e:
        err_msg = str(e) or f"{type(e).__name__}: {repr(e)}"
        logger.error("Direct message error: %s", err_msg)
        return {"error": err_msg}


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
    `audio_source`: Can be a YouTube URL, local audio file path, or direct audio stream URL.
    """
    import subprocess
    subprocess.run(["pkill", "-f", "play_song.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    script_path = os.path.join(os.path.dirname(__file__), "play_song.py")
    python_bin = sys.executable

    proc = subprocess.Popen(
        [python_bin, script_path, str(channel_id), str(audio_source)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    return {
        "success": True,
        "status": "playing",
        "channel_id": str(channel_id),
        "source": audio_source,
        "pid": proc.pid,
    }


@mcp.tool()
async def discord_stop_audio(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Stops currently playing audio in Discord voice channels."""
    import subprocess
    subprocess.run(["pkill", "-f", "play_song.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"success": True, "message": "Audio playback stopped."}


@mcp.tool()
async def discord_voice_status(guild_id: Optional[str] = None) -> Dict[str, Any]:
    """Returns the current voice connection and playback status."""
    gid = _resolve_guild_id(guild_id)
    return voice.get_status(gid)


# ---------------------------------------------------------
# Lifecycle & Execution Runners
# ---------------------------------------------------------

async def _connect_discord_client() -> bool:
    """Connects to Discord Gateway and waits for readiness."""
    if not CONFIG.bot_token:
        logger.error("DISCORD_BOT_TOKEN environment variable is missing or blank!")
        return False

    asyncio.create_task(bot.start(CONFIG.bot_token))
    timeout = 25.0
    while not bot.is_ready() and timeout > 0:
        await asyncio.sleep(0.5)
        timeout -= 0.5

    return bot.is_ready()


async def run_mcp_server():
    """Runs standard Model Context Protocol (MCP) server over stdio."""
    logger.info("Initializing MCP Server transport...")
    ready = await _connect_discord_client()
    if ready:
        logger.info("Discord Bot is ready for MCP requests.")
    else:
        logger.warning("Discord connection pending; starting MCP server anyway.")

    await mcp.run_stdio_async()


PID_FILE = Path(__file__).parent / ".serve.pid"


def _is_pid_alive(pid: int) -> bool:
    """Checks if a process ID is currently running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_running_server_pids() -> List[int]:
    """Finds PIDs of running server.py instances for this workspace, excluding current process."""
    current_pid = os.getpid()
    server_path = str(Path(__file__).resolve())
    found_pids: List[int] = []

    # 1. Check PID file
    if PID_FILE.exists():
        try:
            stored_pid = int(PID_FILE.read_text().strip())
            if stored_pid != current_pid and _is_pid_alive(stored_pid):
                found_pids.append(stored_pid)
        except Exception:
            pass

    # 2. Check system process table via pgrep
    try:
        res = subprocess.run(["pgrep", "-f", f".*{server_path}.*"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                if line.strip():
                    p = int(line.strip())
                    if p != current_pid and p not in found_pids and _is_pid_alive(p):
                        found_pids.append(p)
    except Exception:
        pass

    return found_pids


def run_stop_command(force: bool = False):
    """Stops running Discord Bot server instances / daemon."""
    pids = get_running_server_pids()
    if not pids:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
        print("No running Discord Bot server daemon or process found.")
        return

    sig_name = "SIGKILL" if force else "SIGTERM"
    sig = signal.SIGKILL if force else signal.SIGTERM
    print(f"Stopping {len(pids)} running Discord Bot server process(es): {pids}...")

    for pid in pids:
        try:
            os.kill(pid, sig)
            print(f"  Sent {sig_name} to PID {pid}")
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"  Error signaling PID {pid}: {e}")

    # Wait up to 3 seconds for graceful shutdown
    deadline = time.time() + 3.0
    alive = pids
    while time.time() < deadline and alive:
        time.sleep(0.3)
        alive = [p for p in alive if _is_pid_alive(p)]

    # Force kill any stubborn processes
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"  Force stopped PID {pid} with SIGKILL")
        except Exception:
            pass

    if PID_FILE.exists():
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    print("Discord Bot server stopped successfully.")


async def run_agent_server():
    """Runs the Discord Bot as an AI Agent gateway."""
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        logger.warning("Could not write PID file: %s", e)

    logger.info("==================================================")
    logger.info("Starting Discord AI Agent Gateway Server")
    logger.info("PID:           %d", os.getpid())
    logger.info("Backend Agent: %s", CONFIG.agent_backend)
    logger.info("Allowed Users: %s", ", ".join(sorted(CONFIG.allowed_users)) if CONFIG.allowed_users else "NONE (All ignored)")
    logger.info("==================================================")

    CONFIG.agent_mode = True
    try:
        ready = await _connect_discord_client()
        if not ready:
            logger.error("Failed to connect to Discord! Check your DISCORD_BOT_TOKEN.")
            sys.exit(1)

        logger.info("Bot '%s' is ONLINE and listening for mentions and DMs...", bot.user)
        # Keep running until cancelled
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Agent Server stopped by user.")
    finally:
        if PID_FILE.exists():
            try:
                if PID_FILE.read_text().strip() == str(os.getpid()):
                    PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass


async def run_status_check():
    """Runs a quick one-shot status check and reports connection state."""
    print("Connecting to Discord Gateway to check credentials...", file=sys.stderr)
    ready = await _connect_discord_client()
    
    print("\n" + "=" * 55)
    print("Discord Bot Connection Status")
    print("=" * 55)
    running_pids = get_running_server_pids()
    if running_pids:
        print(f"Local Server:   RUNNING (PID: {', '.join(map(str, running_pids))})")
    else:
        print(f"Local Server:   STOPPED")

    if ready and bot.user:
        ping = round(bot.latency * 1000, 2) if bot.latency else 0.0
        print(f"Gateway Status: VALID / CONNECTED")
        print(f"Bot Username:   {bot.user}")
        print(f"Bot User ID:    {bot.user.id}")
        print(f"Gateway Ping:   {ping} ms")
        print(f"Joined Guilds:  {len(bot.guilds)}")
        for g in bot.guilds:
            print(f"  - {g.name} (ID: {g.id}) | Members: {g.member_count}")
        print(f"Allowed Users:  {len(CONFIG.allowed_users)} configured: {', '.join(sorted(CONFIG.allowed_users)) or 'None'}")
        print(f"Agent Backend:  {CONFIG.agent_backend}")
        print("=" * 55)
        await bot.close()
        sys.exit(0)
    else:
        print("Gateway Status: INVALID / FAILED TO CONNECT")
        print("Reason:         Could not connect to Discord. Verify DISCORD_BOT_TOKEN in .env")
        print("=" * 55)
        sys.exit(1)


# ---------------------------------------------------------
# CLI Arguments Parser
# ---------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discord Bot MCP Server & AI Agent Gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command mode to run")

    # Command: serve
    serve_parser = subparsers.add_parser("serve", help="Run Discord bot as an autonomous AI Agent gateway")
    serve_parser.add_argument(
        "-d", "--daemon",
        action="store_true",
        help="Run detached in the background as a daemon process",
    )
    serve_parser.add_argument(
        "-u", "--allowed-users",
        help="Comma-separated Discord User IDs allowed to interact with the bot (e.g. 1502871150676676749,123456789)",
        type=str,
        default=None,
    )
    serve_parser.add_argument(
        "-a", "--agent",
        help="AI backend tool to invoke (antigravity, gemini, claude, ollama, custom)",
        default=None,
    )
    serve_parser.add_argument("--agent-bin", help="Path to AI tool executable", default=None)
    serve_parser.add_argument("-m", "--model", help="AI model name override", default=None)
    serve_parser.add_argument("--system-prompt", help="Custom Discord formatting system prompt", default=None)
    serve_parser.add_argument("--silent-reject", action="store_true", help="Silently drop unauthorized messages")

    # Command: stop
    stop_parser = subparsers.add_parser("stop", help="Stop running Discord Bot server instances / daemon")
    stop_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Force kill processes immediately with SIGKILL",
    )

    # Command: mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run as an MCP stdio server for AI coding tools")
    mcp_parser.add_argument(
        "-u", "--allowed-users",
        help="Comma-separated Discord User IDs allowed to interact with the bot",
        type=str,
        default=None,
    )
    mcp_parser.add_argument("--enable-agent", action="store_true", help="Also enable background agent responder in MCP mode")

    # Command: status
    subparsers.add_parser("status", help="Test credentials and print live Discord connection status")

    return parser


def main():
    parser = build_cli_parser()
    args, unknown = parser.parse_known_args()

    # If flags like --allowed-users or --agent were passed to root or serve
    if hasattr(args, "allowed_users") and args.allowed_users:
        CONFIG.allowed_users = _parse_user_ids(args.allowed_users)

    if hasattr(args, "agent") and args.agent:
        CONFIG.agent_backend = args.agent.strip().lower()

    if hasattr(args, "agent_bin") and args.agent_bin:
        CONFIG.agent_bin = args.agent_bin

    if hasattr(args, "model") and args.model:
        CONFIG.agent_model = args.model

    if hasattr(args, "system_prompt") and args.system_prompt:
        CONFIG.system_prompt = args.system_prompt

    if hasattr(args, "silent_reject") and args.silent_reject:
        CONFIG.silent_reject = True

    # Mode Dispatcher:
    # 1) stop -> stop daemon / running servers
    if args.command == "stop":
        run_stop_command(force=getattr(args, "force", False))
        return

    # 2) status -> quick CLI check
    if args.command == "status":
        asyncio.run(run_status_check())
        return

    # 3) serve -> interactive or background daemon agent mode
    if args.command == "serve":
        if getattr(args, "daemon", False):
            log_file = Path(__file__).parent / "serve.log"
            python_bin = sys.executable
            script_path = str(Path(__file__).resolve())

            daemon_cmd = [python_bin, script_path, "serve"]
            if hasattr(args, "allowed_users") and args.allowed_users:
                daemon_cmd.extend(["-u", args.allowed_users])
            if hasattr(args, "agent") and args.agent:
                daemon_cmd.extend(["-a", args.agent])
            if hasattr(args, "agent_bin") and args.agent_bin:
                daemon_cmd.extend(["--agent-bin", args.agent_bin])
            if hasattr(args, "model") and args.model:
                daemon_cmd.extend(["-m", args.model])
            if hasattr(args, "system_prompt") and args.system_prompt:
                daemon_cmd.extend(["--system-prompt", args.system_prompt])
            if hasattr(args, "silent_reject") and args.silent_reject:
                daemon_cmd.append("--silent-reject")

            with open(log_file, "a") as f_out:
                proc = subprocess.Popen(
                    daemon_cmd,
                    stdout=f_out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=str(Path(__file__).parent),
                )
            PID_FILE.write_text(str(proc.pid))
            print(f"Discord Agent Gateway Server started in background (PID: {proc.pid})")
            print(f"Logs: {log_file}")
            print("To stop: python server.py stop")
            return

        try:
            asyncio.run(run_agent_server())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Agent Server shutdown.")
        return

    # 4) default or mcp -> standard MCP stdio mode
    if hasattr(args, "enable_agent") and args.enable_agent:
        CONFIG.agent_mode = True

    try:
        asyncio.run(run_mcp_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("MCP Server shutdown.")


if __name__ == "__main__":
    main()
