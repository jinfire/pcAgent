import json
from types import SimpleNamespace

from app.config import ModelSettings
from app.llm import openai_client


class FakeResponses:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            tool_call = SimpleNamespace(
                type="function_call",
                name="echo",
                arguments=json.dumps({"text": "hello"}),
                call_id="call_1",
            )
            return SimpleNamespace(output=[tool_call], output_text="")
        assert kwargs["input"][-1]["type"] == "function_call_output"
        return SimpleNamespace(output=[], output_text="finished")


def test_tool_loop_executes_and_returns_final(monkeypatch) -> None:
    fake = SimpleNamespace(responses=FakeResponses())
    monkeypatch.setattr(openai_client, "get_openai_client", lambda: fake)
    events = []

    result = openai_client.run_tool_loop(
        model_settings=ModelSettings("openai", "test-model", "medium"),
        instructions="test",
        user_input="go",
        tools=[],
        execute_tool=lambda name, args: json.dumps({"ok": True, "value": args["text"]}),
        max_steps=3,
        on_progress=events.append,
    )

    assert result == "finished"
    assert [event["status"] for event in events] == ["running", "completed"]


class FakeGeminiMessage:
    def __init__(self, content="", tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=True):
        del exclude_none
        return {"role": "assistant", "content": self.content, "tool_calls": []}


class FakeGeminiCompletions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            function = SimpleNamespace(name="echo", arguments=json.dumps({"text": "hello"}))
            call = SimpleNamespace(id="call_1", function=function)
            message = FakeGeminiMessage(tool_calls=[call])
        else:
            assert kwargs["messages"][-1]["role"] == "tool"
            message = FakeGeminiMessage(content="gemini finished")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_gemini_tool_loop_executes_and_returns_final(monkeypatch) -> None:
    completions = FakeGeminiCompletions()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(openai_client, "get_gemini_client", lambda: fake)

    result = openai_client.run_tool_loop(
        model_settings=ModelSettings("gemini", "gemini-test", "medium"),
        instructions="test",
        user_input="go",
        tools=[
            {
                "type": "function",
                "name": "echo",
                "description": "echo",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ],
        execute_tool=lambda name, args: json.dumps({"ok": name == "echo", "value": args["text"]}),
        max_steps=3,
    )

    assert result == "gemini finished"
    assert completions.calls == 2
