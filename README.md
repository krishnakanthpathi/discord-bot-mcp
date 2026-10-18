# Discord Bot MCP Server (`discord-bot-mcp`)

A high-performance, native Python MCP (Model Context Protocol) server powered by `FastMCP` and `discord.py`. Designed specifically for AI coding agents and automated workflows to interact with Discord.

## Capabilities

1. **Auto-Discovery & Bot Identity**:
   - `discord_whoami`: Query the connected bot's user profile, ID, and servers.
   - `discord_list_guilds`: List all accessible servers without needing manual IDs.

2. **Channel & Category Management**:
   - `discord_list_channels`: View all text, voice, and announcement channels grouped by category.
   - `discord_create_text_channel` / `discord_create_voice_channel`: Dynamically provision channels.
   - `discord_delete_channel`: Delete channels by ID.
   - `discord_create_category` / `discord_delete_category`: Manage categories.

3. **Rich Messaging & File Attachments**:
   - `discord_send_message`: Send text, rich embeds, and arbitrary local file attachments (`file_paths=['/path/to/file']`).
   - `discord_read_messages`: Read recent history with attachments and emoji reactions.
   - `discord_add_reaction`: React to messages with emojis.

4. **Voice Channel Audio Streaming**:
   - `discord_join_voice`: Connect the bot to any voice channel.
   - `discord_leave_voice`: Disconnect from voice.
   - `discord_play_audio`: Stream local audio files (MP3, WAV) or web audio streams via FFmpeg.
   - `discord_stop_audio`: Stop current playback.
   - `discord_voice_status`: Inspect connection and playback state.

---

## Antigravity MCP Configuration

Add this entry to your `~/.gemini/config/mcp_config.json`:

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
