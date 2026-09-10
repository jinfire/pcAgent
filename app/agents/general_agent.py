from __future__ import annotations

from app.config import Settings
from app.llm.openai_client import ProgressCallback, run_tool_loop
from app.tools.general_tools import GENERAL_TOOL_DEFINITIONS, GeneralTools

GENERAL_INSTRUCTIONS = """
You are the Web Reader Agent. Read a specific public HTTP(S) URL when the task includes one.
You are not a search engine and cannot access local services, private networks, credentials, authenticated systems,
or JavaScript-only page state. Do not invent a URL. Return a concise, factual result to the Manager and clearly
state limitations.
""".strip()


def run_general_agent(task: str, settings: Settings, on_progress: ProgressCallback | None = None) -> str:
    tools = GeneralTools(max_output_chars=settings.max_tool_output_chars)
    return run_tool_loop(
        model_settings=settings.general,
        instructions=GENERAL_INSTRUCTIONS,
        user_input=task,
        tools=GENERAL_TOOL_DEFINITIONS,
        execute_tool=tools.execute,
        max_steps=settings.max_agent_steps,
        on_progress=on_progress,
        role="general",
    )
