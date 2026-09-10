import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.storage import AgentDatabase
from app.tools import coding_tools
from app.tools.coding_tools import CodingTools


def git(tmp_path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(tmp_path), *args],
        check=True,
        capture_output=True,
        shell=False,
    )


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "test@example.com")
    git(repository, "config", "user.name", "Test")
    (repository / "app.py").write_text("print('a')\n", encoding="utf-8")
    (repository / ".env").write_text("SECRET=old\n", encoding="utf-8")
    git(repository, "add", "app.py", ".env")
    git(repository, "commit", "-m", "initial")
    return repository


def test_git_diff_report_filters_sensitive_files(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    (repository / "app.py").write_text("print('a')\nprint('b')\n", encoding="utf-8")
    (repository / ".env").write_text("SECRET=new-value\n", encoding="utf-8")

    report = CodingTools(repository).git_diff_report()

    assert report["additions"] == 1
    assert report["deletions"] == 0
    assert [item["path"] for item in report["files"]] == ["app.py"]
    assert "print('b')" in report["diff"]
    assert "SECRET" not in report["diff"]
    with pytest.raises(PermissionError):
        CodingTools(repository).git_diff(file=".env")


def test_ripgrep_search_uses_direct_executable_and_keeps_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "src").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".env").write_text("SECRET=value", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(coding_tools.shutil, "which", lambda name: "rg.exe")

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, b"src/app.py:1:1:needle\n", b"")

    monkeypatch.setattr(coding_tools.subprocess, "run", fake_run)
    tools = CodingTools(repository)
    result = tools.code_search("needle; whoami", "src")

    assert "src/app.py" in result
    assert captured["argv"][0] == "rg.exe"
    assert "needle; whoami" in captured["argv"]
    assert captured["kwargs"]["shell"] is False
    with pytest.raises(PermissionError):
        tools.code_search("needle", str(outside))
    with pytest.raises(PermissionError):
        tools.code_search("needle", ".env")


def test_git_api_and_invalid_workspace(tmp_path: Path, monkeypatch) -> None:
    repository = make_repository(tmp_path)
    (repository / "app.py").write_text("print('changed')\n", encoding="utf-8")
    database = AgentDatabase(
        tmp_path / "agent.db", allowed_workspace_roots=(repository,)
    )
    database.initialize()
    workspace = database.create_workspace("Repo", str(repository))
    monkeypatch.setattr(main, "get_database", lambda: database)
    client = TestClient(app)

    status = client.get(f"/api/workspaces/{workspace['id']}/git/status")
    assert status.status_code == 200
    assert status.json()["files"][0]["path"] == "app.py"
    diff = client.get(f"/api/workspaces/{workspace['id']}/git/diff")
    assert diff.status_code == 200
    assert "changed" in diff.json()["diff"]
    escaped = client.get(
        f"/api/workspaces/{workspace['id']}/git/diff", params={"file": "../outside.py"}
    )
    assert escaped.status_code == 403
    assert client.get("/api/workspaces/missing/git/status").status_code == 404


def test_commit_requires_explicit_confirmation_and_excludes_sensitive_changes(
    tmp_path: Path, monkeypatch
) -> None:
    repository = make_repository(tmp_path)
    (repository / "app.py").write_text("print('safe change')\n", encoding="utf-8")
    (repository / ".env").write_text("SECRET=must-not-commit\n", encoding="utf-8")
    database = AgentDatabase(tmp_path / "agent.db", allowed_workspace_roots=(repository,))
    database.initialize()
    workspace = database.create_workspace("Repo", str(repository))
    monkeypatch.setattr(main, "get_database", lambda: database)
    client = TestClient(app)
    endpoint = f"/api/workspaces/{workspace['id']}/git/commit"

    assert client.post(endpoint, json={"message": "approved", "confirmed": False}).status_code == 422
    committed = client.post(endpoint, json={"message": "approved", "confirmed": True})
    assert committed.status_code == 200
    assert committed.json()["commit"]
    assert committed.json()["files"] == ["app.py"]

    committed_env = subprocess.run(
        ["git", "-C", str(repository), "show", "HEAD:.env"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout
    assert committed_env == "SECRET=old\n"
    assert CodingTools(repository).git_status_details()["files"] == []
