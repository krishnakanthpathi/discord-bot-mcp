"""
Agent Bridge for Discord Bot.
Executes AI CLI tools (Antigravity 'agy', Gemini, Claude, Ollama, custom)
and formats responses cleanly for Discord.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from typing import List, Optional

logger = logging.getLogger("discord-bot-mcp.agent_bridge")

DEFAULT_DISCORD_SYSTEM_PROMPT = (
    "You are an AI assistant replying directly to a user in Discord.\n"
    "Format your response cleanly for Discord chat:\n"
    "- Use concise, readable Discord markdown (bold, bullet points, clean numbered lists).\n"
    "- Always use syntax-highlighted code blocks (```python, ```bash, etc.) for code.\n"
    "- Keep responses direct, well-structured, and punchy without unnecessary conversational filler.\n"
    "- Make sure formatting displays well on both desktop and mobile Discord."
)

# Regex to strip terminal ANSI escape sequences
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_ansi(text: str) -> str:
    """Removes ANSI color and style escape sequences from terminal output."""
    if not text:
        return ""
    return ANSI_ESCAPE_RE.sub("", text)


def split_discord_message(text: str, limit: int = 1950) -> List[str]:
    """
    Splits long messages into chunks that fit Discord's 2000 character limit.
    Maintains code block formatting across chunks and guarantees no empty strings.
    """
    text = (text or "").strip()
    if not text:
        return []

    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0
    in_code_block = False
    code_lang = ""

    lines = text.splitlines(keepends=True)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = stripped[3:].strip()
            else:
                in_code_block = False
                code_lang = ""

        # If adding this line exceeds the limit
        if current_len + len(line) > limit:
            if in_code_block:
                current_chunk.append("\n```\n")
            joined = "".join(current_chunk).strip()
            if joined:
                chunks.append(joined)
            current_chunk = []
            if in_code_block:
                current_chunk.append(f"```{code_lang}\n")
                current_len = len(current_chunk[0])
            else:
                current_len = 0

        current_chunk.append(line)
        current_len += len(line)

    if current_chunk:
        joined = "".join(current_chunk).strip()
        if joined:
            chunks.append(joined)

    return [c for c in chunks if c and c.strip()]


class AgentBridge:
    """
    Bridges Discord queries to AI agent CLIs.
    """

    def __init__(
        self,
        backend: str = "antigravity",
        agent_bin: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        custom_cmd: Optional[str] = None,
        timeout: float = 180.0,
    ):
        self.backend = backend.lower()
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_DISCORD_SYSTEM_PROMPT
        self.custom_cmd = custom_cmd
        self.timeout = timeout
        self.agent_bin = agent_bin or self._detect_binary()

    def _detect_binary(self) -> str:
        """Finds the executable path for the requested backend."""
        if self.backend in ("antigravity", "agy"):
            for candidate in ["agy", "/Users/krishnakanth/.local/bin/agy", "/usr/local/bin/agy"]:
                found = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
                if found:
                    return found
            return "agy"
        elif self.backend == "gemini":
            return shutil.which("gemini") or "/opt/homebrew/bin/gemini"
        elif self.backend == "claude":
            return shutil.which("claude") or "/opt/homebrew/bin/claude"
        elif self.backend == "ollama":
            return shutil.which("ollama") or "/usr/local/bin/ollama"
        elif self.backend == "codex":
            return shutil.which("codex") or "codex"
        return self.backend

    async def execute(self, user_prompt: str, user_name: Optional[str] = None) -> str:
        """
        Executes the AI tool with prompt and returns formatted response text.
        Guarantees a non-empty string is returned.
        """
        formatted_prompt = (
            f"[System Instructions]\n{self.system_prompt}\n\n"
            f"[Discord Context]\nUser: {user_name or 'Discord User'}\n\n"
            f"[User Message]\n{user_prompt}"
        )

        cmd: List[str] = []

        if self.backend in ("antigravity", "agy"):
            cmd = [
                self.agent_bin,
                "-p",
                formatted_prompt,
                "--dangerously-skip-permissions",
                "--disable-slash-commands",
            ]
            if self.model:
                cmd.extend(["--model", self.model])

        elif self.backend == "gemini":
            cmd = [self.agent_bin, "-p", formatted_prompt]
            if self.model:
                cmd.extend(["-m", self.model])

        elif self.backend == "claude":
            cmd = [self.agent_bin, "-p", formatted_prompt]
            if self.model:
                cmd.extend(["--model", self.model])

        elif self.backend == "ollama":
            model_name = self.model or "llama3"
            cmd = [self.agent_bin, "run", model_name, formatted_prompt]

        elif self.backend == "custom" and self.custom_cmd:
            proc = await asyncio.create_subprocess_shell(
                self.custom_cmd.replace("{prompt}", user_prompt),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            out_str = clean_ansi(stdout.decode("utf-8", errors="replace")).strip()
            if proc.returncode != 0:
                err_str = clean_ansi(stderr.decode("utf-8", errors="replace")).strip()
                raise RuntimeError(f"Custom command failed (code {proc.returncode}): {err_str}")
            return out_str or "*(Command executed successfully with no output)*"

        else:
            cmd = [self.agent_bin, "-p", formatted_prompt]

        logger.info("Executing CLI command: %s (backend: %s)", cmd[0], self.backend)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise TimeoutError(f"Agent execution timed out after {self.timeout}s")

        out_str = clean_ansi(stdout.decode("utf-8", errors="replace")).strip()
        err_str = clean_ansi(stderr.decode("utf-8", errors="replace")).strip()

        if proc.returncode != 0:
            msg = err_str or out_str or f"Process exited with code {proc.returncode}"
            raise RuntimeError(f"Agent ({self.backend}) error: {msg}")

        # If stdout was completely empty, fallback to stderr or friendly placeholder
        if not out_str:
            if err_str:
                out_str = err_str
            else:
                out_str = "🤖 Hey there! I received your message, but the agent produced an empty reply. Please try asking again or rephrasing!"

        return out_str
