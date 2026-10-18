"""
SQLite queue for incoming Discord messages and mentions.
Allows Antigravity or background tasks to poll unread incoming requests.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_FILE = Path(__file__).parent / "discord_inbox.sqlite3"


def get_connection(db_path: Path = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_FILE) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT,
                guild_id TEXT,
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                is_mention INTEGER DEFAULT 0,
                attachments_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending', -- pending, processed, ignored
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON inbox_messages(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channel ON inbox_messages(channel_id)")
        conn.commit()


def enqueue_message(
    message_id: str,
    channel_id: str,
    channel_name: str,
    guild_id: Optional[str],
    author_id: str,
    author_name: str,
    content: str,
    is_mention: bool = False,
    attachments: Optional[List[str]] = None,
    db_path: Path = DB_FILE,
) -> bool:
    """Inserts a new incoming message if not already present."""
    init_db(db_path)
    attachments_json = json.dumps(attachments or [])
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO inbox_messages (
                    message_id, channel_id, channel_name, guild_id,
                    author_id, author_name, content, is_mention,
                    attachments_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    str(message_id),
                    str(channel_id),
                    channel_name,
                    str(guild_id) if guild_id else None,
                    str(author_id),
                    author_name,
                    content,
                    1 if is_mention else 0,
                    attachments_json,
                    now,
                ),
            )
            conn.commit()
            return True
    except sqlite3.Error:
        return False


def get_pending(limit: int = 20, channel_id: Optional[str] = None, db_path: Path = DB_FILE) -> List[Dict[str, Any]]:
    """Retrieves pending messages ordered chronologically."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        if channel_id:
            cursor = conn.execute(
                """
                SELECT * FROM inbox_messages
                WHERE status = 'pending' AND channel_id = ?
                ORDER BY id ASC LIMIT ?
                """,
                (str(channel_id), limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM inbox_messages
                WHERE status = 'pending'
                ORDER BY id ASC LIMIT ?
                """,
                (limit,),
            )
        rows = cursor.fetchall()

    results = []
    for r in rows:
        results.append({
            "message_id": r["message_id"],
            "channel_id": r["channel_id"],
            "channel_name": r["channel_name"],
            "guild_id": r["guild_id"],
            "author_id": r["author_id"],
            "author_name": r["author_name"],
            "content": r["content"],
            "is_mention": bool(r["is_mention"]),
            "attachments": json.loads(r["attachments_json"] or "[]"),
            "created_at": r["created_at"],
        })
    return results


def mark_processed(message_id: str, status: str = "processed", db_path: Path = DB_FILE) -> bool:
    """Marks a message as processed or ignored."""
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE inbox_messages
            SET status = ?, processed_at = ?
            WHERE message_id = ?
            """,
            (status, now, str(message_id)),
        )
        conn.commit()
        return cursor.rowcount > 0
