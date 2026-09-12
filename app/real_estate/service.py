from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import Settings, get_settings
from app.llm.openai_client import ProgressCallback
from app.real_estate.analyst import analyze_real_estate
from app.real_estate.jobs import RealEstateJobRunner
from app.real_estate.models import ALL_ANALYSIS_MODES, MVP_ANALYSIS_MODES
from app.real_estate.repository import RealEstateRepository
from app.real_estate.sources import build_adapters


class RealEstateService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = RealEstateRepository(settings.database_path)
        self.adapters = build_adapters(
            molit_api_key=settings.molit_api_key,
            rone_api_key=settings.rone_api_key,
            timeout_seconds=settings.real_estate_source_timeout_seconds,
            max_bytes=settings.real_estate_source_max_bytes,
        )
        self.runner = RealEstateJobRunner(
            self.repository,
            self.adapters,
            policy_feed_urls=settings.real_estate_policy_feed_urls,
        )
        self.initialize()

    def initialize(self) -> None:
        self.repository.initialize()
        for adapter in self.adapters.values():
            self.repository.upsert_source(adapter.registry_record())

    def start(self) -> None:
        self.runner.start()

    def stop(self) -> None:
        self.runner.stop()

    def capabilities(self) -> dict[str, Any]:
        return {
            "agent": "Korea Real Estate Analyst",
            "analysis_modes": [
                {"id": mode, "implemented": mode in MVP_ANALYSIS_MODES}
                for mode in ALL_ANALYSIS_MODES
            ],
            "reliability_levels": {
                "A": "법령·고시·정부 원문·공식 통계",
                "B": "공공기관·금융기관 연구자료",
                "C": "허용된 민간 가격·매물 데이터",
                "D": "언론 기사",
                "E": "유튜브·블로그·커뮤니티·개인 의견",
            },
            "source_statuses": [
                "ready", "configuration_required", "temporarily_unavailable", "disabled", "error"
            ],
        }

    def enqueue(self, payload: dict[str, Any]) -> dict[str, Any]:
        job = self.repository.create_job(payload)
        self.runner.wake()
        return job

    def analyze(
        self,
        *,
        question: str,
        analysis_mode: str,
        region: str,
        comparison_regions: list[str] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        watchlist_id: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if watchlist_id:
            watchlist = self.repository.get_watchlist(watchlist_id)
            if not region and watchlist["regions"]:
                region = str(watchlist["regions"][0])
            if not comparison_regions:
                comparison_regions = [str(item) for item in watchlist["comparison_regions"]]
        if not region.strip():
            raise ValueError("A region is required")
        return analyze_real_estate(
            question=question,
            analysis_mode=analysis_mode,
            region=region.strip()[:100],
            comparison_regions=[str(item).strip()[:100] for item in (comparison_regions or []) if str(item).strip()],
            period_start=period_start,
            period_end=period_end,
            watchlist_id=watchlist_id,
            repository=self.repository,
            settings=self.settings,
            on_progress=on_progress,
        )


@lru_cache(maxsize=1)
def get_real_estate_service() -> RealEstateService:
    return RealEstateService(get_settings())


def run_real_estate_agent(
    task: str,
    analysis_mode: str,
    region: str,
    comparison_regions: list[str],
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> str:
    service = RealEstateService(settings)
    result = service.analyze(
        question=task,
        analysis_mode=analysis_mode,
        region=region,
        comparison_regions=comparison_regions,
        on_progress=on_progress,
    )
    return json.dumps(result, ensure_ascii=False)
