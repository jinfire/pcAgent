from dataclasses import replace

from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.main import app
from app.real_estate.service import RealEstateService


def service(tmp_path) -> RealEstateService:
    settings = replace(
        get_settings(),
        database_path=tmp_path / "agent.db",
        gemini_api_key="",
        openai_api_key="",
        molit_api_key="",
        rone_api_key="",
        real_estate_policy_feed_urls=(),
    )
    return RealEstateService(settings)


def test_watchlist_source_job_and_analysis_api_contract(tmp_path, monkeypatch) -> None:
    real_estate = service(tmp_path)
    monkeypatch.setattr(main, "get_real_estate_service", lambda: real_estate)
    client = TestClient(app)

    capabilities = client.get("/api/real-estate/capabilities")
    assert capabilities.status_code == 200
    assert sum(item["implemented"] for item in capabilities.json()["analysis_modes"]) == 4

    sources = client.get("/api/real-estate/sources")
    assert sources.status_code == 200
    assert {item["id"] for item in sources.json()} == {
        "molit_apartment_trade", "rone_market_index", "official_policy"
    }
    assert next(item for item in sources.json() if item["id"] == "molit_apartment_trade")["status"] == "configuration_required"

    created = client.post(
        "/api/real-estate/watchlists",
        json={
            "name": "동탄",
            "regions": ["동탄"],
            "comparison_regions": ["분당"],
            "district_codes": {"동탄": "41590"},
        },
    )
    assert created.status_code == 201
    watchlist_id = created.json()["id"]
    assert client.get("/api/real-estate/watchlists").json()[0]["id"] == watchlist_id
    renamed = {**created.json(), "name": "동탄 수정"}
    assert client.patch(f"/api/real-estate/watchlists/{watchlist_id}", json=renamed).json()["name"] == "동탄 수정"

    job = client.post(
        "/api/real-estate/jobs",
        json={
            "source_id": "molit_apartment_trade",
            "period_start": "2026-08",
            "period_end": "2026-08",
            "region": "동탄",
            "district_code": "41590",
        },
    )
    assert job.status_code == 202
    assert client.get(f"/api/real-estate/jobs/{job.json()['id']}").status_code == 200
    blocked_key = client.post(
        "/api/real-estate/jobs",
        json={
            "source_id": "molit_apartment_trade",
            "period_start": "2026-08",
            "period_end": "2026-08",
            "region": "동탄",
            "parameters": {"serviceKey": "do-not-send"},
        },
    )
    assert blocked_key.status_code == 422

    response = client.post(
        "/api/real-estate/analyze",
        json={
            "question": "동탄 시장 흐름은?",
            "analysis_mode": "market_trend",
            "region": "동탄",
            "watchlist_id": watchlist_id,
        },
    )
    assert response.status_code == 200
    assert '"type": "real_estate_final"' in response.text
    assert '"data_status": "insufficient_data"' in response.text
    assert client.get("/api/real-estate/reports/latest", params={"watchlist_id": watchlist_id}).status_code == 200

    assert client.delete(f"/api/real-estate/watchlists/{watchlist_id}").status_code == 204
