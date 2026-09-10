from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "agent.db",
    "agent.db-shm",
    "agent.db-wal",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
ALLOWED_EXECUTABLES = {
    "python", "py", "pytest", "node", "npm", "npx", "pnpm", "yarn",
    "dotnet", "cargo", "go", "java", "javac", "mvn", "mvnw",
    "gradle", "gradlew",
}
SHELL_META = {"&", "|", ">", "<", ";", "`", "\n", "\r"}


class CodingTools:
    def __init__(self, root: Path, timeout_seconds: int = 120, max_output_chars: int = 20_000):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("AGENT_WORKSPACE_ROOT must be a directory")
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "search_files": self.search_files,
            "code_search": self.code_search,
            "run_command": self.run_command,
            "git_status": self.git_status,
            "git_diff": self.git_diff,
        }
        if name not in handlers:
            raise ValueError(f"Unknown coding tool: {name}")
        result = handlers[name](**arguments)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False)

    def list_files(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_dir():
            raise ValueError("Path is not a directory")
        items: list[str] = []
        for current, directories, files in os.walk(target):
            directories[:] = sorted(d for d in directories if d not in SKIP_DIRECTORIES)
            current_path = Path(current)
            for directory in directories:
                items.append(self._relative(current_path / directory) + "/")
                if len(items) >= 300:
                    return "\n".join(items) + "\n... truncated"
            for filename in sorted(files):
                candidate = current_path / filename
                if self._is_sensitive(candidate):
                    continue
                items.append(self._relative(candidate))
                if len(items) >= 300:
                    return "\n".join(items) + "\n... truncated"
        return "\n".join(items) or "(empty)"

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        self._assert_not_sensitive(target)
        if not target.is_file():
            raise ValueError("Path is not a file")
        if target.stat().st_size > 1_000_000:
            raise ValueError("File is larger than 1 MB")
        content = target.read_text(encoding="utf-8", errors="replace")
        return self._truncate(content)

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        self._assert_not_sensitive(target)
        encoded = content.encode("utf-8")
        if len(encoded) > 1_000_000:
            raise ValueError("Content is larger than 1 MB")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return f"Wrote {len(encoded)} bytes to {self._relative(target)}"

    def search_files(self, query: str, path: str) -> str:
        return self.code_search(query=query, path=path)

    def code_search(self, query: str, path: str) -> str:
        if not query or len(query) > 500:
            raise ValueError("Query must contain 1-500 characters")
        target = self._resolve(path)
        if not target.exists():
            raise ValueError("Search path does not exist")
        self._assert_not_sensitive(target)
        executable = shutil.which("rg")
        if not executable:
            raise RuntimeError("ripgrep (rg) is not installed or is not on PATH")
        argv = [
            executable,
            "--line-number",
            "--column",
            "--no-heading",
            "--color",
            "never",
            "--fixed-strings",
            "--ignore-case",
            "--hidden",
            "--glob",
            "!.git/**",
            "--glob",
            "!.ssh/**",
            "--glob",
            "!.aws/**",
            "--glob",
            "!.env",
            "--glob",
            "!.env.*",
            "--glob",
            "!*.pem",
            "--glob",
            "!*.key",
            "--glob",
            "!*.p12",
            "--glob",
            "!*.pfx",
            "--glob",
            "!*.kdbx",
            "--max-filesize",
            "1M",
            "--",
            query,
            self._relative(target),
        ]
        try:
            completed = subprocess.run(
                argv,
                cwd=self.root,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
                env=self._safe_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            partial = self._decode(exc.stdout or b"") + self._decode(exc.stderr or b"")
            return self._truncate(f"Timed out after {self.timeout_seconds}s\n{partial}")
        output = self._decode(completed.stdout) + self._decode(completed.stderr)
        if completed.returncode == 1:
            return "No matches"
        if completed.returncode != 0:
            return self._truncate(f"ripgrep failed with exit code {completed.returncode}\n{output}")
        return self._truncate(output) or "No matches"

    def run_command(self, command: str, cwd: str) -> str:
        working_directory = self._resolve(cwd)
        if not working_directory.is_dir():
            raise ValueError("cwd is not a directory")
        argv = self._validate_command(command)
        try:
            completed = subprocess.run(
                argv,
                cwd=working_directory,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
                env=self._safe_environment(),
            )
            output = self._decode(completed.stdout) + self._decode(completed.stderr)
            return self._truncate(f"Exit code: {completed.returncode}\n{output}")
        except subprocess.TimeoutExpired as exc:
            partial = self._decode(exc.stdout or b"") + self._decode(exc.stderr or b"")
            return self._truncate(f"Timed out after {self.timeout_seconds}s\n{partial}")

    def git_status(self, path: str) -> str:
        details = self.git_status_details(path)
        lines = [f"Branch: {details['branch'] or '(detached)'}"]
        lines.extend(f"{item['status']} {item['path']}" for item in details["files"])
        return "\n".join(lines) if details["files"] else lines[0] + "\nWorking tree clean"

    def git_status_details(self, path: str = ".") -> dict[str, Any]:
        repository = self._repository(path)
        branch_result = self._git_process(repository, ["branch", "--show-current"])
        status_result = self._git_process(
            repository, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )
        if status_result.returncode != 0:
            raise ValueError(self._decode(status_result.stderr).strip() or "Not a git repository")
        records = self._decode(status_result.stdout).split("\x00")
        files: list[dict[str, str]] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 4:
                continue
            status = record[:2]
            relative = record[3:]
            if status[0] in {"R", "C"} and index < len(records):
                index += 1
            if self._safe_git_relative(relative):
                files.append({"status": status, "path": Path(relative).as_posix()})
        return {
            "branch": self._decode(branch_result.stdout).strip(),
            "clean": not files,
            "files": files,
        }

    def git_diff(self, path: str = ".", file: str | None = None) -> str:
        return str(self.git_diff_report(path=path, file=file)["diff"])

    def git_diff_report(self, path: str = ".", file: str | None = None) -> dict[str, Any]:
        repository = self._repository(path)
        status = self.git_status_details(path)
        safe_paths = [str(item["path"]) for item in status["files"]]
        if file is not None:
            selected = self._resolve(file)
            self._assert_not_sensitive(selected)
            selected_relative = self._relative(selected)
            if selected_relative not in safe_paths:
                safe_paths = [selected_relative]
            else:
                safe_paths = [selected_relative]
        if not safe_paths:
            return {**status, "stat": "", "diff": "", "additions": 0, "deletions": 0}

        base_args = self._diff_base(repository)
        patch = self._git_process(
            repository,
            [*base_args, "--no-ext-diff", "--no-textconv", "--", *safe_paths],
        )
        stat = self._git_process(repository, [*base_args, "--stat", "--", *safe_paths])
        numstat = self._git_process(repository, [*base_args, "--numstat", "--", *safe_paths])
        additions = 0
        deletions = 0
        for line in self._decode(numstat.stdout).splitlines():
            columns = line.split("\t", 2)
            if len(columns) >= 2:
                additions += int(columns[0]) if columns[0].isdigit() else 0
                deletions += int(columns[1]) if columns[1].isdigit() else 0
        return {
            **status,
            "stat": self._truncate(self._decode(stat.stdout) + self._decode(stat.stderr)),
            "diff": self._truncate(self._decode(patch.stdout) + self._decode(patch.stderr)),
            "additions": additions,
            "deletions": deletions,
        }

    def git_diff_stat(self, path: str = ".") -> str:
        return str(self.git_diff_report(path)["stat"])

    def git_changed_files(self, path: str = ".") -> list[dict[str, str]]:
        return list(self.git_status_details(path)["files"])

    def user_commit(self, message: str, path: str = ".") -> dict[str, Any]:
        """Commit only after an explicit API/UI request. This is intentionally not an Agent tool."""
        cleaned_message = " ".join(message.split()).strip()
        if not cleaned_message or len(cleaned_message) > 200:
            raise ValueError("Commit message must contain 1-200 characters")
        if any(ord(character) < 32 for character in message):
            raise ValueError("Commit message cannot contain control characters or newlines")
        repository = self._repository(path)
        safe_paths = [item["path"] for item in self.git_status_details(path)["files"]]
        if not safe_paths:
            raise ValueError("There are no safe workspace changes to commit")
        add_result = self._git_process(repository, ["add", "--", *safe_paths])
        if add_result.returncode != 0:
            detail = self._decode(add_result.stderr) or self._decode(add_result.stdout)
            raise RuntimeError(f"git add failed: {self._truncate(detail).strip()}")
        commit_result = self._git_process(
            repository,
            [
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--only",
                "--no-verify",
                "-m",
                cleaned_message,
                "--",
                *safe_paths,
            ],
        )
        output = self._decode(commit_result.stdout) + self._decode(commit_result.stderr)
        if commit_result.returncode != 0:
            raise RuntimeError(f"git commit failed: {self._truncate(output).strip()}")
        revision = self._git_process(repository, ["rev-parse", "--short", "HEAD"])
        return {
            "commit": self._decode(revision.stdout).strip(),
            "message": cleaned_message,
            "files": safe_paths,
            "output": self._truncate(output),
        }

    def _run_git(self, path: str, args: list[str]) -> str:
        repository = self._repository(path)
        completed = self._git_process(repository, args)
        output = self._decode(completed.stdout) + self._decode(completed.stderr)
        return self._truncate(f"Exit code: {completed.returncode}\n{output}")

    def _repository(self, path: str) -> Path:
        repository = self._resolve(path)
        if not repository.is_dir():
            raise ValueError("Repository path is not a directory")
        result = self._git_process(repository, ["rev-parse", "--is-inside-work-tree"])
        if result.returncode != 0 or self._decode(result.stdout).strip() != "true":
            detail = (self._decode(result.stderr) or self._decode(result.stdout)).strip()
            raise ValueError(f"Workspace is not a git repository: {detail[:300]}")
        return repository

    def _git_process(self, repository: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            timeout=self.timeout_seconds,
            shell=False,
            check=False,
            env=self._safe_environment(),
        )

    def _diff_base(self, repository: Path) -> list[str]:
        head = self._git_process(repository, ["rev-parse", "--verify", "HEAD"])
        return ["diff", "HEAD"] if head.returncode == 0 else ["diff"]

    def _safe_git_relative(self, relative: str) -> bool:
        try:
            candidate = self._resolve(relative)
        except (PermissionError, ValueError):
            return False
        return not self._is_sensitive(candidate)

    def _resolve(self, user_path: str) -> Path:
        if not isinstance(user_path, str) or "\x00" in user_path:
            raise ValueError("Invalid path")
        raw = Path(user_path or ".")
        candidate = raw if raw.is_absolute() else self.root / raw
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("Path is outside AGENT_WORKSPACE_ROOT") from exc
        return resolved

    def _relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix() or "."

    def _is_sensitive(self, path: Path) -> bool:
        relative_parts = [part.casefold() for part in path.parts]
        name = path.name.casefold()
        protected_location = (
            ".git" in relative_parts
            or ".ssh" in relative_parts
            or ".aws" in relative_parts
        )
        if protected_location:
            return True
        if name == ".env.example":
            return False
        return (
            name.startswith(".env")
            or name in SENSITIVE_NAMES
            or path.suffix.casefold() in SENSITIVE_SUFFIXES
        )

    def _assert_not_sensitive(self, path: Path) -> None:
        if self._is_sensitive(path):
            raise PermissionError("Access to credential or repository metadata files is blocked")

    def _validate_command(self, command: str) -> list[str]:
        if not command.strip() or len(command) > 2_000:
            raise ValueError("Command must contain 1-2000 characters")
        if any(character in command for character in SHELL_META):
            raise PermissionError("Shell operators, redirection, and multiline commands are blocked")
        try:
            argv = shlex.split(command, posix=False)
        except ValueError as exc:
            raise ValueError("Command could not be parsed") from exc
        argv = [self._strip_quotes(item) for item in argv]
        if not argv:
            raise ValueError("Command is empty")
        executable = Path(argv[0]).name.casefold()
        for suffix in (".exe", ".cmd", ".bat"):
            if executable.endswith(suffix):
                executable = executable[: -len(suffix)]
        if executable not in ALLOWED_EXECUTABLES:
            raise PermissionError(f"Executable is not allowed: {executable}")
        lowered = [arg.casefold() for arg in argv[1:]]
        if any(arg in {"-c", "-command", "-encodedcommand"} for arg in lowered):
            raise PermissionError("Inline script execution is blocked")
        if any(".." in Path(arg).parts for arg in argv[1:] if arg and not arg.startswith("-")):
            raise PermissionError("Parent path arguments are blocked")
        for arg in argv[1:]:
            if arg and not arg.startswith("-") and Path(arg).is_absolute():
                self._resolve(arg)
        return argv

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        sensitive_fragments = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
        environment = {
            name: value
            for name, value in os.environ.items()
            if not any(fragment in name.upper() for fragment in sensitive_fragments)
            and not name.upper().startswith("GIT_CONFIG_")
        }
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        environment["PYTHONNOUSERSITE"] = "1"
        return environment

    @staticmethod
    def _strip_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    @staticmethod
    def _decode(value: bytes) -> str:
        if not value:
            return ""
        for encoding in ("utf-8", "cp949"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_output_chars:
            return value
        return value[: self.max_output_chars] + "\n... output truncated"


CODING_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function", "name": "list_files",
        "description": "Recursively list files under a directory in the allowed workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "read_file",
        "description": "Read a UTF-8 text file in the allowed workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "write_file",
        "description": "Create or replace a UTF-8 text file in the allowed workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "search_files",
        "description": "Search code with ripgrep using a literal string inside the workspace.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query", "path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "code_search",
        "description": "Search symbols, functions, classes, and strings with ripgrep inside the workspace.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}}, "required": ["query", "path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "run_command",
        "description": "Run one allowlisted development command without a shell. No pipes or redirection.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command", "cwd"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "git_status",
        "description": "Show read-only git status for a repository in the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function", "name": "git_diff",
        "description": "Show the unstaged git diff for a repository in the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
]
