from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# USD per 1M tokens, checked against official pricing on 2026-09-10.
# Gemini 3.8 Flash uses its introductory rate through 2026-12-31.
PRICE_PER_MILLION: dict[str, tuple[float, float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75, 0.075),
    "gpt-5.4-mini": (0.75, 4.50, 0.075),
    "gpt-5.6-sol": (4.00, 20.00, 0.40),
}

_lock = threading.Lock()


def record_usage(
    *,
    path: Path,
    provider: str,
    model: str,
    role: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    estimated_cost = estimate_cost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "role": role,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost,
    }
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def estimate_cost(
    *, model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
) -> float | None:
    rates = PRICE_PER_MILLION.get(model)
    if rates is None:
        return None
    input_rate, output_rate, cached_rate = rates
    uncached = max(input_tokens - cached_input_tokens, 0)
    value = (uncached * input_rate + cached_input_tokens * cached_rate + output_tokens * output_rate) / 1_000_000
    return round(value, 8)


def usage_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "requests": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "by_role": {},
    }
    if not path.is_file():
        return summary
    with _lock:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        summary["requests"] += 1
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            summary[key] += int(event.get(key) or 0)
        cost = event.get("estimated_cost_usd")
        if isinstance(cost, (int, float)):
            summary["estimated_cost_usd"] += float(cost)
        role = str(event.get("role") or "unknown")
        role_summary = summary["by_role"].setdefault(role, {"requests": 0, "estimated_cost_usd": 0.0})
        role_summary["requests"] += 1
        if isinstance(cost, (int, float)):
            role_summary["estimated_cost_usd"] += float(cost)
    summary["estimated_cost_usd"] = round(summary["estimated_cost_usd"], 8)
    for role_summary in summary["by_role"].values():
        role_summary["estimated_cost_usd"] = round(role_summary["estimated_cost_usd"], 8)
    return summary
