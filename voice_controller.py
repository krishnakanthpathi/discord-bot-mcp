"""
Voice connection and audio playback controller using discord.py and FFmpeg.
Supports joining voice channels, streaming audio files, and web streams.
"""

from __future__ import annotations
import asyncio
import os
import shutil
from typing import Dict, Optional
import discord

FFMPEG_EXECUTABLE = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


class VoiceController:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        # guild_id -> VoiceClient
        self.voice_clients: Dict[int, discord.VoiceClient] = {}
        # guild_id -> current playing track info
        self.current_track: Dict[int, str] = {}

    async def join_channel(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Connects or moves the bot to the specified voice channel."""
        guild_id = channel.guild.id
        existing_vc = channel.guild.voice_client

        if existing_vc and existing_vc.is_connected():
            if existing_vc.channel.id != channel.id:
                await existing_vc.move_to(channel)
            self.voice_clients[guild_id] = existing_vc
            return existing_vc

        vc = await channel.connect(timeout=20.0, reconnect=True)
        self.voice_clients[guild_id] = vc
        return vc

    async def leave_channel(self, guild_id: int) -> bool:
        """Disconnects the bot from the guild voice channel."""
        vc = self.voice_clients.pop(guild_id, None)
        if not vc:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client

        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            await vc.disconnect(force=True)
            self.current_track.pop(guild_id, None)
            return True
        return False

    async def play_audio(
        self,
        voice_channel: discord.VoiceChannel,
        audio_source: str,
        volume: float = 1.0,
    ) -> Dict[str, str]:
        """Plays a local audio file or stream URL in the voice channel."""
        vc = await self.join_channel(voice_channel)
        guild_id = voice_channel.guild.id

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        is_url = audio_source.startswith("http://") or audio_source.startswith("https://")
        if not is_url and not os.path.exists(audio_source):
            raise FileNotFoundError(f"Audio file not found at path: {audio_source}")

        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" if is_url else ""
        options = f"-filter:a volume={volume}"

        def _after_playback(error: Optional[Exception]) -> None:
            if error:
                import sys
                print(f"[VoiceController] Playback error: {error}", file=sys.stderr)
            self.current_track.pop(guild_id, None)

        audio_player = discord.FFmpegPCMAudio(
            audio_source,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_options if is_url else None,
            options=options,
        )

        vc.play(audio_player, after=_after_playback)
        self.current_track[guild_id] = audio_source

        return {
            "status": "playing",
            "channel_id": str(voice_channel.id),
            "channel_name": voice_channel.name,
            "source": audio_source,
        }

    def stop_audio(self, guild_id: int) -> bool:
        """Stops currently playing audio."""
        vc = self.voice_clients.get(guild_id)
        if not vc:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            self.current_track.pop(guild_id, None)
            return True
        return False

    def get_status(self, guild_id: int) -> Dict[str, any]:
        """Returns the current voice status for the given guild."""
        vc = self.voice_clients.get(guild_id)
        if not vc:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                vc = guild.voice_client

        if not vc or not vc.is_connected():
            return {"connected": False, "channel": None, "playing": False}

        return {
            "connected": True,
            "channel_id": str(vc.channel.id),
            "channel_name": vc.channel.name,
            "is_playing": vc.is_playing(),
            "is_paused": vc.is_paused(),
            "current_track": self.current_track.get(guild_id),
        }
