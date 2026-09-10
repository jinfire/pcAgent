from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.main import MessageRequest, app
from app.storage import AgentDatabase


def make_database(tmp_path: Path) -> tuple[AgentDatabase, dict, Path]:
    allowed = tmp_path / "projects"
    allowed.mkdir()
    project = allowed / "one"
    project.mkdir()
    database = AgentDatabase(
        tmp_path / "data" / "agent.db",
        allowed_workspace_roots=(allowed,),
    )
    database.initialize()
    workspace = database.create_workspace("One", str(project))
    return database, workspace, allowed


def test_session_crud_and_messages(tmp_path: Path) -> None:
    database, workspace, _ = make_database(tmp_path)

    session = database.create_session("First", workspace["id"], "agent")
    database.add_message(session["id"], "user", "hello")
    database.add_message(session["id"], "assistant", "hi", {"kind": "result"})

    loaded = database.get_session(session["id"])
    assert loaded["workspace_id"] == workspace["id"]
    assert [item["content"] for item in loaded["messages"]] == ["hello", "hi"]
    assert loaded["messages"][1]["metadata"] == {"kind": "result"}

    renamed = database.rename_session(session["id"], "Renamed")
    assert renamed["name"] == "Renamed"
    assert database.list_sessions(workspace["id"])[0]["id"] == session["id"]

    database.delete_session(session["id"])
    assert database.list_sessions() == []


def test_workspace_registration_boundary(tmp_path: Path) -> None:
    database, _, allowed = make_database(tmp_path)
    nested = allowed / "two"
    nested.mkdir()
    assert database.create_workspace("Two", str(nested))["root_path"] == str(nested.resolve())

    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        database.create_workspace("Outside", str(outside))
    except PermissionError:
        pass
    else:
        raise AssertionError("Outside workspace should be blocked")


def test_session_selects_its_workspace_and_ignores_browser_history(
    tmp_path: Path, monkeypatch
) -> None:
    database, first, allowed = make_database(tmp_path)
    second_path = allowed / "two"
    second_path.mkdir()
    second = database.create_workspace("Two", str(second_path))
    session = database.create_session("Second session", second["id"], "agent")
    database.add_message(session["id"], "user", "server history")
    monkeypatch.setattr(main, "get_database", lambda: database)

    messages, selected_session, settings = main._prepare_request(
        MessageRequest(
            message="continue",
            history=[{"role": "user", "content": "untrusted browser history"}],
            session_id=session["id"],
            workspace_id=second["id"],
        ),
        "agent",
    )

    assert selected_session == session["id"]
    assert settings.agent_workspace_root == second_path.resolve()
    assert [item["content"] for item in messages] == ["server history", "continue"]
    assert first["id"] != second["id"]


def test_session_api_crud_and_invalid_workspace(tmp_path: Path, monkeypatch) -> None:
    database, workspace, _ = make_database(tmp_path)
    monkeypatch.setattr(main, "get_database", lambda: database)
    client = TestClient(app)

    created = client.post(
        "/api/sessions",
        json={"name": "API session", "workspace_id": workspace["id"], "mode": "chat"},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    assert client.patch(f"/api/sessions/{session_id}", json={"name": "New name"}).json()["name"] == "New name"
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204

    invalid = client.post(
        "/api/sessions",
        json={"name": "Bad", "workspace_id": "missing", "mode": "agent"},
    )
    assert invalid.status_code == 404

    outside = tmp_path / "outside-api"
    outside.mkdir()
    blocked = client.post(
        "/api/workspaces", json={"name": "Outside", "root_path": str(outside)}
    )
    assert blocked.status_code == 403
