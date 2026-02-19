"""
BAKU_MASTER — Memoria persistente (SQLite).

Almacena:
  - tasks        : cola de trabajo del agente
  - activity_log : historial de acciones y eventos
  - agent_memory : pares clave/valor para estado a largo plazo
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path("/home/user/AgenciaBaku/baku_memory.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            type         TEXT DEFAULT 'general',
            status       TEXT DEFAULT 'pending',
            priority     INTEGER DEFAULT 5,
            result       TEXT,
            error        TEXT,
            created_at   TEXT,
            started_at   TEXT,
            finished_at  TEXT,
            scheduled_for TEXT
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT,
            level      TEXT DEFAULT 'info',
            message    TEXT,
            data       TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_memory (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT
        );
        """)


# ── Tasks ──────────────────────────────────────────────────────────────────────

def create_task(
    title: str,
    description: str = "",
    task_type: str = "general",
    priority: int = 5,
    scheduled_for: Optional[str] = None,
) -> dict:
    task_id = str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            """INSERT INTO tasks
               (id, title, description, type, status, priority, created_at, scheduled_for)
               VALUES (?,?,?,?,?,?,?,?)""",
            (task_id, title, description, task_type, "pending", priority, _now(), scheduled_for),
        )
    return {"id": task_id, "title": title, "status": "pending"}


def get_pending_tasks(limit: int = 5) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, title, description, type, priority
               FROM tasks
               WHERE status = 'pending'
               ORDER BY priority DESC, created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_task_status(
    task_id: str,
    status: str,
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now = _now()
    with _conn() as con:
        if status == "running":
            con.execute(
                "UPDATE tasks SET status=?, started_at=? WHERE id=?",
                (status, now, task_id),
            )
        else:
            con.execute(
                "UPDATE tasks SET status=?, finished_at=?, result=?, error=? WHERE id=?",
                (status, now, result, error, task_id),
            )


def get_all_tasks(limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, title, description, type, status, priority,
                      result, error, created_at, started_at, finished_at
               FROM tasks ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Activity log ───────────────────────────────────────────────────────────────

def log_activity(
    message: str,
    level: str = "info",
    task_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO activity_log (task_id, level, message, data, created_at)
               VALUES (?,?,?,?,?)""",
            (task_id, level, message, json.dumps(data) if data else None, _now()),
        )


def get_recent_activity(limit: int = 30) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, task_id, level, message, data, created_at
               FROM activity_log ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Agent memory (key/value) ───────────────────────────────────────────────────

def set_memory(key: str, value) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO agent_memory (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value), _now()),
        )


def get_memory(key: str, default=None):
    with _conn() as con:
        row = con.execute(
            "SELECT value FROM agent_memory WHERE key=?", (key,)
        ).fetchone()
    return json.loads(row["value"]) if row else default


def get_all_memory() -> dict:
    with _conn() as con:
        rows = con.execute("SELECT key, value, updated_at FROM agent_memory").fetchall()
    return {r["key"]: {"value": json.loads(r["value"]), "updated_at": r["updated_at"]} for r in rows}
