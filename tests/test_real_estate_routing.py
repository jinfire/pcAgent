import json
from types import SimpleNamespace

from app.agents import manager


def test_manager_exposes_and_routes_to_one_real_estate_agent(monkeypatch) -> None:
    names = [item["name"] for item in manager.MANAGER_TOOLS]
    assert names.count("run_real_estate_analyst") == 1

    captured = {}

    def fake_agent(task, analysis_mode, region, comparison_regions, settings, on_progress):
        captured.update(
            task=task,
            analysis_mode=analysis_mode,
            region=region,
            comparison_regions=comparison_regions,
        )
        return json.dumps({"status": "warning"})

    def fake_loop(**kwargs):
        return kwargs["execute_tool"](
            "run_real_estate_analyst",
            {
                "task": "동탄 시장은?",
                "analysis_mode": "market_trend",
                "region": "동탄",
                "comparison_regions": ["분당"],
            },
        )

    monkeypatch.setattr(manager, "run_real_estate_agent", fake_agent)
    monkeypatch.setattr(manager, "run_tool_loop", fake_loop)
    settings = SimpleNamespace(manager=object(), max_agent_steps=4)
    result = manager.run_manager("동탄 시장은?", settings)

    assert json.loads(result)["status"] == "warning"
    assert captured["analysis_mode"] == "market_trend"
    assert captured["region"] == "동탄"
