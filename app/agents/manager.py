from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.coding_agent import run_coding_agent
from app.agents.escalation_agent import run_escalation_agent
from app.agents.general_agent import run_general_agent
from app.agents.review_agent import run_review_agent
from app.config import Settings
from app.llm.openai_client import ProgressCallback, run_tool_loop
from app.real_estate.service import run_real_estate_agent

logger = logging.getLogger(__name__)

MANAGER_INSTRUCTIONS = """
You are the Architect and Manager for a small personal PC software agent. Understand the request, choose a design,
delegate work, and own the final answer. For local repository/file/command work, call the Coding Agent. Its work is
automatically checked by an independent Review Agent. Call the Web Reader only when the user provides a specific
public HTTP(S) URL that must be read; it is not a search engine. You may answer simple knowledge questions directly.
Route Korean real-estate market, apartment comparison, buy/sell scenario, and policy-impact questions to the
Korea Real Estate Analyst. It uses stored official evidence and deterministic calculations; do not send these tasks
to the Coding Agent. Ask the tool for only an implemented MVP mode.

Use the Senior Adjudicator only when you are genuinely unsure about an important architecture decision, or when the
worker and reviewer reach materially different conclusions. Do not use it for ordinary defects or routine validation.
Never pretend to inspect or change local state yourself. Normally call a worker once with a complete task. Review all
returned evidence, distinguish completed work from limitations, and give a concise final response in Korean when appropriate.
Before calling the Coding Agent, create a concrete 3-7 step execution plan. Pass it in the plan argument in execution order,
including inspection, implementation, tests, and review when relevant.
""".strip()

MANAGER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "run_real_estate_analyst",
        "description": "Analyze Korean real-estate market, apartment comparison, buy/sell scenarios, or policy impact using stored official evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "analysis_mode": {
                    "type": "string",
                    "enum": ["market_trend", "apartment_comparison", "buy_or_sell_scenario", "policy_analysis"],
                },
                "region": {"type": "string"},
                "comparison_regions": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            },
            "required": ["task", "analysis_mode", "region", "comparison_regions"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_coding_agent",
        "description": "Delegate local code/file work. An independent GPT review runs automatically afterward.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 8,
                },
            },
            "required": ["task", "plan"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_web_reader",
        "description": "Read a specific public HTTP(S) URL included in the task. This is not web search.",
        "parameters": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_senior_adjudicator",
        "description": "Resolve major architecture uncertainty or a material disagreement between agents. Expensive; use rarely.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["question", "evidence"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def run_manager(user_input: str, settings: Settings, on_progress: ProgressCallback | None = None) -> str:
    if on_progress:
        on_progress({"type": "status", "message": "Gemini Architect가 요청을 분석하고 있습니다..."})

    def execute(name: str, arguments: dict[str, Any]) -> str:
        if name == "run_coding_agent":
            plan = arguments.get("plan")
            return _run_coding_pipeline(
                str(arguments["task"]),
                settings,
                on_progress,
                plan=[str(item) for item in plan] if isinstance(plan, list) else None,
            )
        if name == "run_real_estate_analyst":
            _selected(on_progress, "Korea Real Estate Analyst")
            return run_real_estate_agent(
                str(arguments["task"]),
                str(arguments["analysis_mode"]),
                str(arguments["region"]),
                [str(item) for item in arguments.get("comparison_regions") or []],
                settings,
                on_progress,
            )
        if name == "run_web_reader":
            _selected(on_progress, "Web Reader")
            result = run_general_agent(str(arguments["task"]), settings, on_progress)
            return json.dumps({"ok": True, "worker": "Web Reader", "result": result}, ensure_ascii=False)
        if name == "run_senior_adjudicator":
            _selected(on_progress, "Senior Adjudicator")
            result = run_escalation_agent(
                str(arguments["question"]), str(arguments["evidence"]), settings
            )
            return json.dumps(
                {"ok": True, "worker": "Senior Adjudicator", "result": result}, ensure_ascii=False
            )
        raise ValueError(f"Unknown manager tool: {name}")

    def manager_progress(event: dict[str, Any]) -> None:
        if event.get("type") == "tool" and event.get("name") in {
            "run_coding_agent",
            "run_web_reader",
            "run_senior_adjudicator",
            "run_real_estate_analyst",
        }:
            return
        if on_progress:
            on_progress(event)

    result = run_tool_loop(
        model_settings=settings.manager,
        instructions=MANAGER_INSTRUCTIONS,
        user_input=user_input,
        tools=MANAGER_TOOLS,
        execute_tool=execute,
        max_steps=min(settings.max_agent_steps, 4),
        on_progress=manager_progress,
        role="manager",
    )
    if on_progress:
        on_progress({"type": "status", "message": "완료했습니다."})
    return result


def _run_coding_pipeline(
    task: str,
    settings: Settings,
    on_progress: ProgressCallback | None,
    plan: list[str] | None = None,
) -> str:
    tracker = _PlanTracker(plan, on_progress)
    tracker.start()
    _selected(on_progress, "Coding Agent")
    _phase(on_progress, "coding", "running")
    try:
        coding_result = run_coding_agent(task, settings, tracker.forward)
    except Exception:
        tracker.fail_current()
        _phase(on_progress, "coding", "failed")
        raise
    tracker.finish_coding()
    _phase(on_progress, "coding", "done")

    _selected(on_progress, "Test / Review Agent")
    tracker.start_review()
    _phase(on_progress, "review", "running")
    try:
        review = run_review_agent(task, coding_result, settings, on_progress)
    except Exception:
        tracker.fail_current()
        _phase(on_progress, "review", "failed")
        raise
    tracker.finish_review()
    _phase(on_progress, "review", "done")
    if on_progress:
        on_progress({"type": "review", "review": review.as_dict()})
    payload: dict[str, Any] = {
        "ok": True,
        "worker": "Coding Agent",
        "coding_result": coding_result,
        "review": review.as_dict(),
    }

    if settings.auto_escalation_enabled and review.needs_escalation:
        _selected(on_progress, "Senior Adjudicator")
        evidence = json.dumps(
            {"coding_result": coding_result, "review": review.as_dict()}, ensure_ascii=False
        )
        payload["escalation"] = run_escalation_agent(task, evidence, settings)
        payload["auto_escalated"] = True
    else:
        payload["auto_escalated"] = False
    _phase(on_progress, "result", "done")
    return json.dumps(payload, ensure_ascii=False)


def _selected(on_progress: ProgressCallback | None, name: str) -> None:
    logger.info("Selected agent: %s", name)
    if on_progress:
        on_progress({"type": "agent", "name": name})


class _PlanTracker:
    def __init__(self, plan: list[str] | None, callback: ProgressCallback | None) -> None:
        cleaned = [" ".join(item.split())[:200] for item in (plan or []) if item.strip()]
        self.titles = cleaned[:7] or [
            "관련 코드와 보안 경계 확인",
            "요청한 변경 구현",
            "관련 테스트 실행",
            "Reviewer 독립 검증",
        ]
        if not any("review" in title.casefold() or "검토" in title for title in self.titles):
            self.titles.append("Reviewer 독립 검증")
        self.callback = callback
        self.current = 0
        self.review_index = len(self.titles) - 1
        self.statuses = ["pending"] * len(self.titles)
        self.test_seen = False

    def start(self) -> None:
        if not self.callback:
            return
        self.callback(
            {
                "type": "plan",
                "steps": [
                    {"id": f"step-{index + 1}", "title": title, "status": "pending"}
                    for index, title in enumerate(self.titles)
                ],
            }
        )
        self._set(0, "running")

    def forward(self, event: dict[str, Any]) -> None:
        if self.callback:
            self.callback(event)
        if event.get("type") == "tool" and event.get("name") == "run_command":
            self.test_seen = True
            mapped_status = {
                "running": "running",
                "completed": "done",
                "failed": "failed",
            }.get(str(event.get("status")))
            if mapped_status:
                _phase(self.callback, "test", mapped_status)
        if event.get("type") == "tool" and event.get("status") == "completed":
            self._advance_coding()
        elif event.get("type") == "tool" and event.get("status") == "failed":
            self._set(self.current, "failed")

    def finish_coding(self) -> None:
        for index in range(self.review_index):
            if self.statuses[index] in {"pending", "running"}:
                self._set(index, "done")
        if not self.test_seen:
            _phase(self.callback, "test", "failed")

    def start_review(self) -> None:
        self.current = self.review_index
        self._set(self.review_index, "running")

    def finish_review(self) -> None:
        self._set(self.review_index, "done")

    def fail_current(self) -> None:
        self._set(self.current, "failed")

    def _advance_coding(self) -> None:
        if self.current >= self.review_index:
            return
        if self.statuses[self.current] != "failed":
            self._set(self.current, "done")
        next_index = min(self.current + 1, self.review_index - 1)
        self.current = next_index
        if self.statuses[next_index] == "pending":
            self._set(next_index, "running")

    def _set(self, index: int, status: str) -> None:
        if not 0 <= index < len(self.statuses):
            return
        self.statuses[index] = status
        if self.callback:
            self.callback({"type": "plan_step", "id": f"step-{index + 1}", "status": status})


def _phase(on_progress: ProgressCallback | None, name: str, status: str) -> None:
    if on_progress:
        on_progress({"type": "phase", "name": name, "status": status})
