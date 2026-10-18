import asyncio
import os
import sys
import shutil
import subprocess
import discord
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(sys.argv[1] if len(sys.argv) > 1 else "1515016570508935288")
RAW_SOURCE = sys.argv[2] if len(sys.argv) > 2 else "/tmp/stefan_theme.mp3"

AUDIO_FILE = RAW_SOURCE
is_url = RAW_SOURCE.startswith("http://") or RAW_SOURCE.startswith("https://")
if is_url and any(d in RAW_SOURCE.lower() for d in ("youtube.com", "youtu.be", "soundcloud.com")):
    if "b-hvwl" in RAW_SOURCE.lower() and os.path.exists("/tmp/stefan_theme.mp3"):
        AUDIO_FILE = "/tmp/stefan_theme.mp3"
        is_url = False
    else:
        ytdlp_bin = shutil.which("yt-dlp") or "/Library/Frameworks/Python.framework/Versions/3.14/bin/yt-dlp"
        if os.path.exists(ytdlp_bin):
            res = subprocess.run([ytdlp_bin, "-g", "-f", "bestaudio/best", RAW_SOURCE], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                AUDIO_FILE = res.stdout.strip().split("\n")[0]

if not discord.opus.is_loaded():
    candidates = [
        os.getenv("LIBOPUS_PATH"),
        "/opt/homebrew/opt/opus/lib/libopus.dylib",
        "/opt/homebrew/lib/libopus.dylib",
        "/usr/local/lib/libopus.dylib",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                discord.opus.load_opus(c)
                break
            except Exception:
                pass

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True

bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    ch = bot.get_channel(CHANNEL_ID)
    if not ch or not isinstance(ch, discord.VoiceChannel):
        await bot.close()
        return

    vc = await ch.connect(timeout=20.0, reconnect=True)
    playback_done = asyncio.Event()

    def after_play(err):
        playback_done.set()

    ffmpeg_bin = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" if is_url else None
    audio_source = discord.FFmpegPCMAudio(
        AUDIO_FILE,
        executable=ffmpeg_bin,
        before_options=before_options,
    )
    vc.play(audio_source, after=after_play)

    await playback_done.wait()
    try:
        await vc.disconnect(force=True)
    except Exception:
        pass
    await bot.close()

if __name__ == "__main__":
    bot.run(TOKEN)
