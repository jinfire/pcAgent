from pathlib import Path

import pytest

from app.tools.coding_tools import CodingTools


def test_workspace_boundary_and_file_round_trip(tmp_path: Path) -> None:
    tools = CodingTools(tmp_path)
    result = tools.write_file("src/hello.py", "print('hello')\n")
    assert "src/hello.py" in result
    assert tools.read_file("src/hello.py") == "print('hello')\n"

    with pytest.raises(PermissionError):
        tools.read_file("../outside.txt")


def test_sensitive_files_are_blocked(tmp_path: Path) -> None:
    tools = CodingTools(tmp_path)
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    with pytest.raises(PermissionError):
        tools.read_file(".env")
    with pytest.raises(PermissionError):
        tools.write_file(".env.local", "secret")
    with pytest.raises(PermissionError):
        tools.write_file("private.key", "secret")
    with pytest.raises(PermissionError):
        tools.write_file("data/agent.db", "session data")


def test_command_policy(tmp_path: Path) -> None:
    tools = CodingTools(tmp_path)
    output = tools.run_command("python --version", ".")
    assert "Exit code: 0" in output

    with pytest.raises(PermissionError):
        tools.run_command("python -c \"print(1)\"", ".")
    with pytest.raises(PermissionError):
        tools.run_command("git commit -m test", ".")
    with pytest.raises(PermissionError):
        tools.run_command("git push", ".")
    with pytest.raises(PermissionError):
        tools.run_command("git diff", ".")
    with pytest.raises(PermissionError):
        tools.run_command("python --version | more", ".")


def test_absolute_command_argument_cannot_leave_workspace(tmp_path: Path) -> None:
    tools = CodingTools(tmp_path)
    outside = tmp_path.parent / "outside.py"
    with pytest.raises(PermissionError):
        tools.run_command(f'python "{outside}"', ".")
