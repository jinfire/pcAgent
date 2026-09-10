from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agents.manager import run_manager
from app.config import get_settings
from app.llm.openai_client import chat
from app.llm.usage import usage_summary
from app.storage import AgentDatabase, StorageConflictError
from app.tools.coding_tools import CodingTools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="My Agent", version="0.2.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=40)
    session_id: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)


class SessionCreateRequest(BaseModel):
    name: str = Field(default="New session", min_length=1, max_length=100)
    workspace_id: str | None = Field(default=None, max_length=64)
    mode: Literal["chat", "agent"] = "agent"


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root_path: str = Field(min_length=1, max_length=1_000)


class CommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200)
    confirmed: Literal[True]


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    settings = get_settings()
    workspaces = get_database().list_workspaces()
    keys = {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
    }
    chat_ready = settings.has_provider_key(settings.chat.provider)
    agent_ready = all(settings.has_provider_key(item) for item in settings.required_agent_providers())
    return {
        "ok": True,
        "api_key_configured": chat_ready and agent_ready,
        "api_keys": keys,
        "chat_ready": chat_ready,
        "agent_ready": agent_ready,
        "workspace_configured": bool(workspaces),
        "workspace_count": len(workspaces),
        "models": {
            "manager": f"{settings.manager.provider}/{settings.manager.model}:{settings.manager.reasoning_effort}",
            "coding": f"{settings.coding.provider}/{settings.coding.model}:{settings.coding.reasoning_effort}",
            "review": f"{settings.review.provider}/{settings.review.model}:{settings.review.reasoning_effort}",
            "escalation": f"{settings.escalation.provider}/{settings.escalation.model}:{settings.escalation.reasoning_effort}",
        },
    }


@app.get("/api/usage")
def usage() -> dict[str, object]:
    return usage_summary(get_settings().usage_log_path)


@app.get("/api/workspaces")
def list_workspaces() -> list[dict[str, object]]:
    return get_database().list_workspaces()


@app.post("/api/workspaces", status_code=201)
def create_workspace(request: WorkspaceCreateRequest) -> dict[str, object]:
    try:
        return get_database().create_workspace(request.name, request.root_path)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> dict[str, object]:
    try:
        return get_database().get_workspace(workspace_id)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/git/status")
def workspace_git_status(workspace_id: str) -> dict[str, object]:
    try:
        return _workspace_tools(workspace_id).git_status_details()
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/git/diff")
def workspace_git_diff(
    workspace_id: str,
    file: str | None = Query(default=None, min_length=1, max_length=1_000),
) -> dict[str, object]:
    try:
        return _workspace_tools(workspace_id).git_diff_report(file=file)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/git/stat")
def workspace_git_stat(workspace_id: str) -> dict[str, object]:
    try:
        report = _workspace_tools(workspace_id).git_diff_report()
        return {
            "stat": report["stat"],
            "files": report["files"],
            "additions": report["additions"],
            "deletions": report["deletions"],
        }
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/workspaces/{workspace_id}/search")
def workspace_code_search(
    workspace_id: str,
    q: str = Query(min_length=1, max_length=500),
    path: str = Query(default=".", max_length=1_000),
) -> dict[str, str]:
    try:
        return {"result": _workspace_tools(workspace_id).code_search(q, path)}
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.post("/api/workspaces/{workspace_id}/git/commit")
def workspace_git_commit(
    workspace_id: str, request: CommitRequest
) -> dict[str, object]:
    try:
        return _workspace_tools(workspace_id).user_commit(request.message)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.patch("/api/workspaces/{workspace_id}")
def rename_workspace(workspace_id: str, request: RenameRequest) -> dict[str, object]:
    try:
        return get_database().rename_workspace(workspace_id, request.name)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.delete("/api/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: str) -> None:
    try:
        get_database().delete_workspace(workspace_id)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/sessions")
def list_sessions(workspace_id: str | None = None) -> list[dict[str, object]]:
    try:
        return get_database().list_sessions(workspace_id)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.post("/api/sessions", status_code=201)
def create_session(request: SessionCreateRequest) -> dict[str, object]:
    try:
        workspace_id = request.workspace_id or _default_workspace_id()
        return get_database().create_session(request.name, workspace_id, request.mode)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, object]:
    try:
        return get_database().get_session(session_id)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, request: RenameRequest) -> dict[str, object]:
    try:
        return get_database().rename_session(session_id, request.name)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    try:
        get_database().delete_session(session_id)
    except Exception as exc:
        raise _storage_http_error(exc) from exc


@app.post("/api/chat")
def chat_endpoint(request: MessageRequest) -> dict[str, str]:
    try:
        messages, session_id, settings = _prepare_request(request, "chat")
    except Exception as exc:
        raise _storage_http_error(exc) from exc
    logger.info("Chat request: %s", _safe_request_summary(request.message))
    try:
        answer = chat(
            messages,
            settings.chat,
            "You are a helpful general chat assistant. Do not claim to access local files, shells, git, or tools. Respond in the user's language.",
            role="chat",
        )
    except Exception as exc:
        logger.error("OpenAI chat error (%s): %s", type(exc).__name__, _safe_log_text(str(exc)))
        raise HTTPException(status_code=502, detail=_public_error(exc)) from exc
    if session_id:
        get_database().add_message(session_id, "assistant", answer[:20_000])
    return {"answer": answer}


@app.post("/api/agent")
async def agent_endpoint(request: MessageRequest) -> StreamingResponse:
    try:
        messages, session_id, settings = _prepare_request(request, "agent")
    except Exception as exc:
        raise _storage_http_error(exc) from exc
    logger.info("Agent request: %s", _safe_request_summary(request.message))
    context = _manager_context(messages)

    async def event_stream():
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        review_metadata: dict[str, object] | None = None

        def progress(event: dict[str, object]) -> None:
            nonlocal review_metadata
            if event.get("type") == "review" and isinstance(event.get("review"), dict):
                review_metadata = event["review"]
            if event.get("type") == "tool":
                logger.info("Tool %s: %s", event.get("name"), event.get("status"))
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def work() -> None:
            try:
                answer = run_manager(context, settings, progress)
            except Exception as exc:
                logger.error("Agent error (%s): %s", type(exc).__name__, _safe_log_text(str(exc)))
                progress({"type": "error", "message": _public_error(exc)})
            else:
                if session_id:
                    metadata = {"review": review_metadata} if review_metadata else None
                    get_database().add_message(
                        session_id, "assistant", answer[:20_000], metadata
                    )
                progress({"type": "final", "answer": answer})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _manager_context(messages: list[dict[str, str]]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    lines = ["Recent browser-session conversation:"]
    for message in messages[-13:-1]:
        label = "User" if message["role"] == "user" else "Assistant"
        lines.append(f"{label}: {message['content']}")
    lines.append(f"Current user request: {messages[-1]['content']}")
    return "\n\n".join(lines)


def _prepare_request(
    request: MessageRequest, mode: Literal["chat", "agent"]
) -> tuple[list[dict[str, str]], str | None, object]:
    settings = get_settings()
    database = get_database()
    session_id = request.session_id
    workspace_id = request.workspace_id
    if session_id:
        session = database.get_session(session_id)
        if session["mode"] != mode:
            raise StorageConflictError("Session mode does not match this endpoint")
        if workspace_id and workspace_id != session["workspace_id"]:
            raise StorageConflictError("Session belongs to a different workspace")
        workspace_id = str(session["workspace_id"])
        messages = [
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in session["messages"][-40:]
        ]
        database.add_message(session_id, "user", request.message)
    else:
        messages = [message.model_dump() for message in request.history]
    messages.append({"role": "user", "content": request.message})

    if workspace_id:
        workspace = database.get_workspace(workspace_id)
        settings = replace(settings, agent_workspace_root=Path(str(workspace["root_path"])))
    elif mode == "agent":
        workspace = database.get_workspace(_default_workspace_id())
        settings = replace(settings, agent_workspace_root=Path(str(workspace["root_path"])))
    return messages, session_id, settings


@lru_cache(maxsize=1)
def get_database() -> AgentDatabase:
    settings = get_settings()
    database = AgentDatabase(
        settings.database_path,
        allowed_workspace_roots=settings.allowed_workspace_roots,
        default_workspace=settings.agent_workspace_root,
    )
    database.initialize()
    return database


def _default_workspace_id() -> str:
    workspaces = get_database().list_workspaces()
    if not workspaces:
        raise RuntimeError("No workspace is configured")
    return str(workspaces[0]["id"])


def _workspace_tools(workspace_id: str) -> CodingTools:
    workspace = get_database().get_workspace(workspace_id)
    settings = get_settings()
    return CodingTools(
        Path(str(workspace["root_path"])),
        timeout_seconds=settings.command_timeout_seconds,
        max_output_chars=settings.max_tool_output_chars,
    )


def _storage_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc.args[0]))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, StorageConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ValueError, RuntimeError)):
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Storage operation failed")
    return HTTPException(status_code=500, detail="Storage operation failed")


def _safe_request_summary(message: str) -> str:
    return _safe_log_text(" ".join(message.split()))


def _safe_log_text(value: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", value)
    redacted = re.sub(
        r"(?i)(password|passwd|secret|token|api[_ -]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted[:300]


def _public_error(exc: Exception) -> str:
    if isinstance(exc, RuntimeError) and "configured" in str(exc):
        return str(exc)
    return "요청을 처리하지 못했습니다. 서버 로그를 확인하세요."
