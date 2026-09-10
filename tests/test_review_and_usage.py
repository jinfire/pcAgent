import json
from pathlib import Path
from types import SimpleNamespace

from app.agents import manager
from app.agents.review_agent import ReviewDecision
from app.agents.review_agent import ReviewIssue
from app.agents.review_agent import parse_review_decision
from app.llm.usage import estimate_cost, record_usage, usage_summary


def test_review_decision_marks_conflict_for_escalation() -> None:
    decision = parse_review_decision(
        json.dumps(
            {
                "verdict": "conflict",
                "confidence": 0.9,
                "needs_escalation": False,
                "summary": "Evidence disagrees.",
                "issues": ["test failed"],
            }
        )
    )
    assert decision.needs_escalation is True
    assert decision.verdict == "conflict"


def test_structured_review_issues_and_legacy_verdict_are_supported() -> None:
    decision = parse_review_decision(
        json.dumps(
            {
                "status": "warning",
                "confidence": 0.91,
                "summary": "One issue",
                "issues": [
                    {
                        "severity": "high",
                        "file": "app/auth.py",
                        "line": 81,
                        "message": "Missing validation",
                    }
                ],
            }
        )
    )
    payload = decision.as_dict()
    assert payload["status"] == "warning"
    assert payload["verdict"] == "revise"
    assert payload["issues"][0] == {
        "severity": "high",
        "file": "app/auth.py",
        "line": 81,
        "message": "Missing validation",
    }

    legacy = ReviewDecision(
        verdict="approve",
        confidence=1.0,
        needs_escalation=False,
        summary="ok",
        issues=[ReviewIssue(severity="low", message="note")],
        raw="{}",
    )
    assert legacy.as_dict()["status"] == "pass"


def test_usage_is_recorded_and_summarized(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    record_usage(
        path=path,
        provider="openai",
        model="gpt-5.4-mini",
        role="review",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cached_input_tokens=200_000,
    )
    summary = usage_summary(path)
    assert summary["requests"] == 1
    assert summary["by_role"]["review"]["requests"] == 1
    assert summary["estimated_cost_usd"] == estimate_cost(
        model="gpt-5.4-mini",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cached_input_tokens=200_000,
    )


def test_coding_pipeline_auto_escalates_on_review_conflict(monkeypatch) -> None:
    conflict = ReviewDecision(
        verdict="conflict",
        confidence=0.9,
        needs_escalation=True,
        summary="Different conclusions",
        issues=["evidence mismatch"],
        raw="{}",
    )
    monkeypatch.setattr(manager, "run_coding_agent", lambda *args: "coding report")
    monkeypatch.setattr(manager, "run_review_agent", lambda *args: conflict)
    monkeypatch.setattr(manager, "run_escalation_agent", lambda *args: "senior decision")

    result = json.loads(
        manager._run_coding_pipeline(
            "task", SimpleNamespace(auto_escalation_enabled=True), None
        )
    )

    assert result["auto_escalated"] is True
    assert result["escalation"] == "senior decision"
