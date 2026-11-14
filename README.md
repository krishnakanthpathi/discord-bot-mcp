# Discord Bot MCP Server & AI Agent Gateway (`discord-bot-mcp`)

A high-performance Python MCP (Model Context Protocol) server and autonomous AI Agent gateway powered by `FastMCP`, `discord.py`, and local AI agent CLIs (Antigravity `agy`, Gemini, Claude, Ollama).

---

## Capabilities

### 1. Identity & Auto-Discovery
* `discord_status`: Query bot connection validity (`valid: true/false`), ping/latency, bot profile, and server list.
* `discord_whoami`: Backwards-compatible alias for `discord_status`.
* `discord_list_guilds`: List all accessible Discord servers with member and owner info.

### 2. Messaging, DMs & Threads
* `discord_send_direct_message`: Send direct messages (DMs) to any Discord user by their User ID.
* `discord_send_message`: Send messages to channels with rich embeds and local file attachments (`file_paths=['/path/to/file']`).
* `discord_read_messages`: Read recent history with attachments and emoji reactions.
* `discord_create_thread`: Spawn discussion threads on messages or channels.
* `discord_add_reaction`: React to messages with emojis.

### 3. Channel & Category Management
* `discord_list_channels`: View all channels grouped by categories.
* `discord_create_text_channel` / `discord_create_voice_channel`: Provision channels dynamically.
* `discord_delete_channel`: Delete channels by ID.
* `discord_create_category` / `discord_delete_category`: Manage categories.

### 4. Voice Channel Audio Streaming
* `discord_join_voice` / `discord_leave_voice`: Connect and disconnect from voice channels.
* `discord_play_audio`: Stream YouTube audio, local files (MP3/WAV), or direct URLs via FFmpeg.
* `discord_stop_audio`: Stop current audio playback.
* `discord_voice_status`: Inspect voice connection and playback state.

---

## AI Agent Gateway Mode (`serve`)

Run your bot as an autonomous AI assistant in Discord. When mentioned in a channel (`@bot`) or sent a direct message (DM), the bot executes an AI tool CLI (default: Antigravity `agy`) and sends formatted responses back to Discord.

### Security Whitelist (`allowed_users`)
Only user IDs in the whitelist can interact with the bot. Unauthorized users are blocked.

### Running Agent Mode
```bash
# Start Agent Gateway mode in foreground
./.venv/bin/python3 server.py serve

# Start as a detached background daemon
./.venv/bin/python3 server.py serve -d

# Or pass allowed users and backend directly via CLI
./.venv/bin/python3 server.py serve -d --allowed-users 1502871150676676749 --agent antigravity
```

### Stopping the Agent Server / Daemon
```bash
# Gracefully stop running server instances / daemon
./.venv/bin/python3 server.py stop

# Force kill with SIGKILL if unresponsive
./.venv/bin/python3 server.py stop --force
```

### CLI Status Check
Check local server process status, bot token validity, and gateway connection:
```bash
./.venv/bin/python3 server.py status
```

---

## Configuration (`.env`)

```ini
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_DEFAULT_GUILD_ID=your_guild_id_here
FFMPEG_PATH=/opt/homebrew/bin/ffmpeg

# Comma-separated list of Discord User IDs permitted to use the AI agent
DISCORD_ALLOWED_USERS=1502871150676676749

# AI Backend: antigravity (default), gemini, claude, ollama, custom
DISCORD_AGENT_BACKEND=antigravity
# DISCORD_AGENT_BIN=/Users/krishnakanth/.local/bin/agy
# DISCORD_AGENT_MODEL=
# DISCORD_SYSTEM_PROMPT=
# DISCORD_SILENT_REJECT=false
```

---

## Antigravity MCP Configuration

In `~/.gemini/config/mcp_config.json`:

```json
{
  "mcpServers": {
    "discord-bot-mcp": {
      "command": "/Users/krishnakanth/Projects/discord-bot-mcp/.venv/bin/python3",
      "args": [
        "/Users/krishnakanth/Projects/discord-bot-mcp/server.py"
      ],
      "env": {
        "PYTHONPATH": "/Users/krishnakanth/Projects/discord-bot-mcp"
      }
    }
  }
}
```
