from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.real_estate.models import FetchRequest, SourceAdapterError
from app.real_estate.repository import RealEstateRepository
from app.real_estate.sources import SourceAdapter


logger = logging.getLogger(__name__)


class RealEstateJobRunner:
    """One lightweight in-process worker; SQLite claims prevent duplicate concurrent runs."""

    def __init__(
        self,
        repository: RealEstateRepository,
        adapters: dict[str, SourceAdapter],
        *,
        policy_feed_urls: tuple[str, ...] = (),
        poll_seconds: float = 5.0,
    ) -> None:
        self.repository = repository
        self.adapters = adapters
        self.policy_feed_urls = policy_feed_urls
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_schedule_date: date | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="real-estate-ingestion",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> dict[str, Any] | None:
        job = self.repository.claim_next_job()
        if job is None:
            return None
        adapter = self.adapters.get(str(job["source_id"]))
        if adapter is None:
            return self.repository.fail_job(
                str(job["id"]), message="Source adapter is unavailable", retryable=False
            )
        request = FetchRequest(
            period_start=str(job["period_start"]),
            period_end=str(job["period_end"]),
            region=str(job["region"]),
            district_code=job.get("district_code"),
            source_url=job.get("source_url"),
            parameters=job.get("parameters") or {},
        )
        try:
            result = adapter.fetch(request)
            counts = self.repository.save_fetch_result(result)
            self.repository.source_success(adapter.source_id)
            completed = self.repository.complete_job(str(job["id"]))
            completed["saved"] = counts
            return completed
        except SourceAdapterError as exc:
            failed = self.repository.fail_job(
                str(job["id"]), message=str(exc), retryable=exc.retryable
            )
            self.repository.source_failure(
                adapter.source_id,
                message=str(exc),
                retryable=exc.retryable,
                next_retry_at=failed.get("next_retry_at"),
                status=(
                    "configuration_required"
                    if exc.category == "configuration_required"
                    else "temporarily_unavailable" if exc.retryable else "error"
                ),
            )
            return failed
        except Exception as exc:
            logger.exception("Unexpected real estate ingestion failure for %s", adapter.source_id)
            failed = self.repository.fail_job(
                str(job["id"]), message=type(exc).__name__, retryable=True
            )
            self.repository.source_failure(
                adapter.source_id,
                message="Unexpected ingestion error",
                retryable=True,
                next_retry_at=failed.get("next_retry_at"),
            )
            return failed

    def schedule_missing_periods(self, today: date | None = None) -> list[dict[str, Any]]:
        """Schedule watchlist-only catch-up work. It never expands to nationwide collection."""
        current = today or datetime.now(timezone.utc).date()
        created: list[dict[str, Any]] = []
        sources = {item["id"]: item for item in self.repository.list_sources()}
        molit = sources.get("molit_apartment_trade")
        for watchlist in self.repository.list_watchlists():
            district_codes = watchlist.get("district_codes") or {}
            if molit and molit["status"] == "ready":
                start = _backfill_start(molit.get("last_success_at"), current, maximum_days=7)
                for run_date in _date_range(start, current):
                    for region in watchlist.get("regions") or []:
                        district_code = district_codes.get(region)
                        if not district_code:
                            continue
                        created.append(
                            self.repository.create_job(
                                {
                                    "source_id": "molit_apartment_trade",
                                    "job_type": f"daily_backfill:{run_date.isoformat()}",
                                    "period_start": run_date.strftime("%Y-%m"),
                                    "period_end": run_date.strftime("%Y-%m"),
                                    "region": region,
                                    "district_code": district_code,
                                    "parameters": {"watchlist_id": watchlist["id"]},
                                }
                            )
                        )
        policy = sources.get("official_policy")
        if policy and policy["status"] == "ready":
            for url in self.policy_feed_urls:
                created.append(
                    self.repository.create_job(
                        {
                            "source_id": "official_policy",
                            "job_type": f"daily:{current.isoformat()}",
                            "period_start": current.isoformat(),
                            "period_end": current.isoformat(),
                            "region": "대한민국",
                            "source_url": url,
                        }
                    )
                )
        return created

    def _loop(self) -> None:
        while not self._stop.is_set():
            today = datetime.now(timezone.utc).date()
            if today != self._last_schedule_date:
                try:
                    self.schedule_missing_periods(today)
                    self._last_schedule_date = today
                except Exception:
                    logger.exception("Real estate startup backfill scheduling failed")
            processed = self.run_once()
            if processed is None:
                self._wake.wait(self.poll_seconds)
                self._wake.clear()


def _backfill_start(last_success_at: str | None, current: date, *, maximum_days: int) -> date:
    if not last_success_at:
        return current
    try:
        last = datetime.fromisoformat(last_success_at.replace("Z", "+00:00")).date()
    except ValueError:
        return current
    return max(last + timedelta(days=1), current - timedelta(days=maximum_days - 1))


def _date_range(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
