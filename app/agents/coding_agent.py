from __future__ import annotations

from app.config import Settings
from app.llm.openai_client import ProgressCallback, run_tool_loop
from app.tools.coding_tools import CODING_TOOL_DEFINITIONS, CodingTools

CODING_INSTRUCTIONS = """
You are the Coding Agent for one explicitly allowed local workspace. Work carefully and efficiently.
Inspect before editing, make the smallest useful changes, run relevant tests or builds, and inspect git diff when available.
Never claim a file or command changed unless its tool result confirms it. Never request or read credentials.
Do not commit, push, alter system configuration, or work outside the configured workspace.
When done, return a concise report of changes, verification, and any remaining concern to the Manager.
""".strip()


def run_coding_agent(task: str, settings: Settings, on_progress: ProgressCallback | None = None) -> str:
    if settings.agent_workspace_root is None:
        raise RuntimeError("AGENT_WORKSPACE_ROOT is not configured")
    tools = CodingTools(
        settings.agent_workspace_root,
        timeout_seconds=settings.command_timeout_seconds,
        max_output_chars=settings.max_tool_output_chars,
    )
    return run_tool_loop(
        model_settings=settings.coding,
        instructions=CODING_INSTRUCTIONS,
        user_input=task,
        tools=CODING_TOOL_DEFINITIONS,
        execute_tool=tools.execute,
        max_steps=settings.max_agent_steps,
        on_progress=on_progress,
        role="coding",
    )
