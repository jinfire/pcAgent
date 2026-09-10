from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from openai import OpenAI

from app.config import ModelSettings, get_settings
from app.llm.usage import record_usage

ProgressCallback = Callable[[dict[str, Any]], None]
ToolExecutor = Callable[[str, dict[str, Any]], str]


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OpenAI(api_key=settings.openai_api_key)


@lru_cache(maxsize=1)
def get_gemini_client() -> OpenAI:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return OpenAI(
        api_key=settings.gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )


def chat(
    messages: list[dict[str, str]],
    model_settings: ModelSettings,
    instructions: str,
    *,
    role: str = "chat",
) -> str:
    if model_settings.provider == "gemini":
        request_messages = [{"role": "system", "content": instructions}, *messages]
        kwargs: dict[str, Any] = {
            "model": model_settings.model,
            "messages": request_messages,
        }
        if model_settings.reasoning_effort:
            kwargs["reasoning_effort"] = model_settings.reasoning_effort
        response = get_gemini_client().chat.completions.create(**kwargs)
        _record_chat_completions_usage(response, model_settings, role)
        return _message_text(response.choices[0].message.content) or "응답 텍스트가 비어 있습니다."

    kwargs = {
        "model": model_settings.model,
        "instructions": instructions,
        "input": messages,
        "store": False,
    }
    if model_settings.reasoning_effort:
        kwargs["reasoning"] = {"effort": model_settings.reasoning_effort}
    response = get_openai_client().responses.create(**kwargs)
    _record_responses_usage(response, model_settings, role)
    text = response.output_text.strip()
    return text or "응답 텍스트가 비어 있습니다."


def run_tool_loop(
    *,
    model_settings: ModelSettings,
    instructions: str,
    user_input: str,
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_steps: int,
    on_progress: ProgressCallback | None = None,
    role: str = "agent",
) -> str:
    """Run a small, stateless function-calling loop for OpenAI or Gemini."""
    if model_settings.provider == "gemini":
        return _run_gemini_tool_loop(
            model_settings=model_settings,
            instructions=instructions,
            user_input=user_input,
            tools=tools,
            execute_tool=execute_tool,
            max_steps=max_steps,
            on_progress=on_progress,
            role=role,
        )
    return _run_openai_tool_loop(
        model_settings=model_settings,
        instructions=instructions,
        user_input=user_input,
        tools=tools,
        execute_tool=execute_tool,
        max_steps=max_steps,
        on_progress=on_progress,
        role=role,
    )


def _run_openai_tool_loop(
    *,
    model_settings: ModelSettings,
    instructions: str,
    user_input: str,
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_steps: int,
    on_progress: ProgressCallback | None,
    role: str,
) -> str:
    client = get_openai_client()
    input_items: list[Any] = [{"role": "user", "content": user_input}]
    tool_calls_used = 0

    while True:
        kwargs: dict[str, Any] = {
            "model": model_settings.model,
            "instructions": instructions,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
        }
        if model_settings.reasoning_effort:
            kwargs["reasoning"] = {"effort": model_settings.reasoning_effort}
        response = client.responses.create(**kwargs)
        _record_responses_usage(response, model_settings, role)
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            return response.output_text.strip() or "작업 결과를 생성하지 못했습니다."

        input_items.extend(response.output)
        for call in calls:
            if tool_calls_used >= max_steps:
                return _finish_openai_at_limit(client, model_settings, instructions, input_items, role)
            tool_calls_used += 1
            result = _execute_call(
                name=str(call.name),
                raw_arguments=call.arguments or "{}",
                execute_tool=execute_tool,
                on_progress=on_progress,
            )
            input_items.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": result}
            )


def _run_gemini_tool_loop(
    *,
    model_settings: ModelSettings,
    instructions: str,
    user_input: str,
    tools: list[dict[str, Any]],
    execute_tool: ToolExecutor,
    max_steps: int,
    on_progress: ProgressCallback | None,
    role: str,
) -> str:
    client = get_gemini_client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": user_input},
    ]
    chat_tools = [_responses_tool_to_chat_tool(tool) for tool in tools]
    tool_calls_used = 0

    while True:
        kwargs: dict[str, Any] = {
            "model": model_settings.model,
            "messages": messages,
            "tools": chat_tools,
            "tool_choice": "auto",
            "reasoning_effort": model_settings.reasoning_effort,
        }
        response = client.chat.completions.create(**kwargs)
        _record_chat_completions_usage(response, model_settings, role)
        message = response.choices[0].message
        calls = list(message.tool_calls or [])
        if not calls:
            return _message_text(message.content) or "작업 결과를 생성하지 못했습니다."

        messages.append(message.model_dump(exclude_none=True))
        for call in calls:
            if tool_calls_used >= max_steps:
                return _finish_gemini_at_limit(client, model_settings, messages, role)
            tool_calls_used += 1
            result = _execute_call(
                name=str(call.function.name),
                raw_arguments=call.function.arguments or "{}",
                execute_tool=execute_tool,
                on_progress=on_progress,
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})


def _execute_call(
    *,
    name: str,
    raw_arguments: str,
    execute_tool: ToolExecutor,
    on_progress: ProgressCallback | None,
) -> str:
    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
    except (json.JSONDecodeError, ValueError) as exc:
        _progress(on_progress, {"type": "tool", "name": name, "status": "failed"})
        return json.dumps({"ok": False, "error": f"Invalid tool arguments: {exc}"})

    _progress(on_progress, {"type": "tool", "name": name, "status": "running"})
    try:
        result = execute_tool(name, arguments)
    except Exception as exc:  # Tool errors are observations for the model.
        _progress(on_progress, {"type": "tool", "name": name, "status": "failed"})
        return json.dumps(
            {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"},
            ensure_ascii=False,
        )
    _progress(on_progress, {"type": "tool", "name": name, "status": "completed"})
    return result


def _finish_openai_at_limit(
    client: OpenAI,
    model_settings: ModelSettings,
    instructions: str,
    input_items: list[Any],
    role: str,
) -> str:
    input_items.append(
        {
            "role": "developer",
            "content": "The tool-call limit was reached. Report completed work, remaining work, and current state without tools.",
        }
    )
    kwargs: dict[str, Any] = {
        "model": model_settings.model,
        "instructions": instructions,
        "input": input_items,
        "tools": [],
        "store": False,
    }
    if model_settings.reasoning_effort:
        kwargs["reasoning"] = {"effort": model_settings.reasoning_effort}
    response = client.responses.create(**kwargs)
    _record_responses_usage(response, model_settings, role)
    return response.output_text.strip() or "도구 호출 한도에 도달해 작업을 중단했습니다."


def _finish_gemini_at_limit(
    client: OpenAI,
    model_settings: ModelSettings,
    messages: list[dict[str, Any]],
    role: str,
) -> str:
    messages.append(
        {
            "role": "system",
            "content": "The tool-call limit was reached. Report completed work, remaining work, and current state without tools.",
        }
    )
    response = client.chat.completions.create(
        model=model_settings.model,
        messages=messages,
        reasoning_effort=model_settings.reasoning_effort,
    )
    _record_chat_completions_usage(response, model_settings, role)
    return _message_text(response.choices[0].message.content) or "도구 호출 한도에 도달해 작업을 중단했습니다."


def _responses_tool_to_chat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        },
    }


def _record_responses_usage(response: Any, model_settings: ModelSettings, role: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    details = getattr(usage, "input_tokens_details", None)
    _write_usage(
        model_settings=model_settings,
        role=role,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
    )


def _record_chat_completions_usage(response: Any, model_settings: ModelSettings, role: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    _write_usage(
        model_settings=model_settings,
        role=role,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
    )


def _write_usage(
    *,
    model_settings: ModelSettings,
    role: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
) -> None:
    settings = get_settings()
    record_usage(
        path=settings.usage_log_path,
        provider=model_settings.provider,
        model=model_settings.model,
        role=role,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(getattr(part, "text", None), str):
                texts.append(part.text)
        return "".join(texts).strip()
    return ""


def _progress(callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    if callback:
        callback(event)
