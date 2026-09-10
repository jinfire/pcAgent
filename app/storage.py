from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StorageConflictError(RuntimeError):
    pass


class AgentDatabase:
    """Small SQLite repository. Connections are short-lived for thread-safe FastAPI use."""

    def __init__(
        self,
        path: Path,
        *,
        allowed_workspace_roots: tuple[Path, ...],
        default_workspace: Path | None = None,
    ) -> None:
        self.path = path.resolve()
        self.allowed_workspace_roots = tuple(root.resolve(strict=False) for root in allowed_workspace_roots)
        self.default_workspace = default_workspace.resolve(strict=False) if default_workspace else None
        self._initialize_lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        root_path TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                        mode TEXT NOT NULL CHECK (mode IN ('chat', 'agent')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        metadata_json TEXT,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated
                        ON sessions(workspace_id, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_messages_session_id
                        ON messages(session_id, id);

                    -- Reserved for a future JSONL-to-SQLite usage migration. Never stores prompts.
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        role TEXT NOT NULL,
                        input_tokens INTEGER NOT NULL,
                        cached_input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL,
                        estimated_cost_usd REAL
                    );
                    """
                )
            self._initialized = True
            if self.default_workspace and self.default_workspace.is_dir():
                self.ensure_workspace(self.default_workspace.name, self.default_workspace)

    def list_workspaces(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY last_used_at DESC, name COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Workspace not found")
        return dict(row)

    def ensure_workspace(self, name: str, root_path: Path) -> dict[str, Any]:
        resolved = self.validate_workspace_path(root_path)
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE root_path = ?", (str(resolved),)
            ).fetchone()
            if row is not None:
                return dict(row)
            now = _now()
            workspace_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO workspaces(id, name, root_path, created_at, last_used_at) VALUES (?, ?, ?, ?, ?)",
                (workspace_id, _clean_name(name, "Workspace"), str(resolved), now, now),
            )
        return self.get_workspace(workspace_id)

    def create_workspace(self, name: str, root_path: str) -> dict[str, Any]:
        resolved = self.validate_workspace_path(Path(root_path))
        self.initialize()
        try:
            return self.ensure_workspace(name, resolved)
        except sqlite3.IntegrityError as exc:
            raise StorageConflictError("Workspace path is already registered") from exc

    def rename_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE workspaces SET name = ? WHERE id = ?",
                (_clean_name(name, "Workspace"), workspace_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Workspace not found")
        return self.get_workspace(workspace_id)

    def delete_workspace(self, workspace_id: str) -> None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT root_path FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Workspace not found")
            if self.default_workspace and Path(row["root_path"]) == self.default_workspace:
                raise StorageConflictError("The configured default workspace cannot be removed")
            session_count = connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()[0]
            if session_count:
                raise StorageConflictError("Delete the workspace sessions first")
            cursor = connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            if cursor.rowcount == 0:
                raise KeyError("Workspace not found")

    def validate_workspace_path(self, path: Path) -> Path:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("Workspace path does not exist") from exc
        if not resolved.is_dir():
            raise ValueError("Workspace path must be a directory")
        if not self.allowed_workspace_roots:
            raise PermissionError("No workspace registration roots are configured")
        for allowed in self.allowed_workspace_roots:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
        raise PermissionError("Workspace path is outside the configured allowed roots")

    def create_session(self, name: str, workspace_id: str, mode: str) -> dict[str, Any]:
        if mode not in {"chat", "agent"}:
            raise ValueError("Session mode must be chat or agent")
        self.get_workspace(workspace_id)
        now = _now()
        session_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, name, workspace_id, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, _clean_name(name, "New session"), workspace_id, mode, now, now),
            )
        return self.get_session(session_id, include_messages=False)

    def list_sessions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        self.initialize()
        query = (
            "SELECT s.*, w.name AS workspace_name FROM sessions s "
            "JOIN workspaces w ON w.id = s.workspace_id"
        )
        params: tuple[Any, ...] = ()
        if workspace_id:
            self.get_workspace(workspace_id)
            query += " WHERE s.workspace_id = ?"
            params = (workspace_id,)
        query += " ORDER BY s.updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, session_id: str, *, include_messages: bool = True) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, w.name AS workspace_name, w.root_path AS workspace_root_path
                FROM sessions s JOIN workspaces w ON w.id = s.workspace_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Session not found")
            result = dict(row)
            if include_messages:
                messages = connection.execute(
                    "SELECT id, role, content, metadata_json, created_at FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
                result["messages"] = [_message_row(item) for item in messages]
        return result

    def rename_session(self, session_id: str, name: str) -> dict[str, Any]:
        self.initialize()
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?",
                (_clean_name(name, "New session"), now, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Session not found")
        return self.get_session(session_id, include_messages=False)

    def delete_session(self, session_id: str) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise KeyError("Session not found")

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant"}:
            raise ValueError("Message role must be user or assistant")
        if not content or len(content) > 20_000:
            raise ValueError("Message content must contain 1-20000 characters")
        session = self.get_session(session_id, include_messages=False)
        now = _now()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages(session_id, role, content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, metadata_json, now),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            connection.execute(
                "UPDATE workspaces SET last_used_at = ? WHERE id = ?",
                (now, session["workspace_id"]),
            )
            message_id = cursor.lastrowid
        return {
            "id": message_id,
            "role": role,
            "content": content,
            "metadata": metadata,
            "created_at": now,
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _message_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    raw = result.pop("metadata_json", None)
    try:
        result["metadata"] = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        result["metadata"] = None
    return result


def _clean_name(value: str, fallback: str) -> str:
    cleaned = " ".join(str(value).split()).strip()
    return cleaned[:100] or fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
