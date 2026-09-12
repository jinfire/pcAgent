from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.real_estate.models import FetchResult, NormalizedSourceItem, stable_key, utc_now


MIGRATION_VERSION = "2026091201_real_estate_mvp"


class RealEstateRepository:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS source_registry (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        base_url TEXT NOT NULL,
                        publisher TEXT NOT NULL,
                        reliability_level TEXT NOT NULL CHECK (reliability_level IN ('A','B','C','D','E')),
                        status TEXT NOT NULL,
                        configuration_env TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        last_success_at TEXT,
                        last_error TEXT,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        next_retry_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS raw_source_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE RESTRICT,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        source_name TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        publisher TEXT NOT NULL,
                        published_at TEXT,
                        retrieved_at TEXT NOT NULL,
                        event_date TEXT,
                        period TEXT,
                        geographic_scope TEXT,
                        property_type TEXT,
                        reliability_level TEXT NOT NULL,
                        raw_reference TEXT NOT NULL,
                        structured_json TEXT NOT NULL,
                        caveats_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        raw_payload TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_raw_source_scope_period
                        ON raw_source_items(source_id, geographic_scope, period);

                    CREATE TABLE IF NOT EXISTS normalized_indicators (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_item_id INTEGER NOT NULL REFERENCES raw_source_items(id) ON DELETE CASCADE,
                        indicator_type TEXT NOT NULL,
                        region TEXT NOT NULL,
                        property_type TEXT,
                        period TEXT NOT NULL,
                        value REAL NOT NULL,
                        unit TEXT NOT NULL,
                        sample_count INTEGER,
                        status TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        UNIQUE(source_item_id, indicator_type, region, property_type, period)
                    );

                    CREATE TABLE IF NOT EXISTS real_estate_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_item_id INTEGER NOT NULL REFERENCES raw_source_items(id) ON DELETE RESTRICT,
                        transaction_key TEXT NOT NULL UNIQUE,
                        region TEXT NOT NULL,
                        district_code TEXT,
                        apartment_name TEXT NOT NULL,
                        exclusive_area_sqm REAL,
                        deal_amount_krw INTEGER,
                        deal_date TEXT,
                        floor INTEGER,
                        built_year INTEGER,
                        cancelled_at TEXT,
                        correction_type TEXT,
                        raw_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_transactions_region_date
                        ON real_estate_transactions(region, deal_date);

                    CREATE TABLE IF NOT EXISTS real_estate_watchlists (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        regions_json TEXT NOT NULL,
                        complexes_json TEXT NOT NULL,
                        comparison_regions_json TEXT NOT NULL,
                        property_types_json TEXT NOT NULL,
                        district_codes_json TEXT NOT NULL,
                        profile_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS policy_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_item_id INTEGER REFERENCES raw_source_items(id) ON DELETE SET NULL,
                        event_key TEXT NOT NULL UNIQUE,
                        policy_name TEXT NOT NULL,
                        policy_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        announced_at TEXT,
                        passed_at TEXT,
                        effective_at TEXT,
                        affected_regions_json TEXT NOT NULL,
                        affected_property_types_json TEXT NOT NULL,
                        official_source TEXT NOT NULL,
                        expected_transmission_path TEXT,
                        counter_effects_json TEXT NOT NULL,
                        confidence TEXT NOT NULL,
                        last_verified_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS expert_claims (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_item_id INTEGER REFERENCES raw_source_items(id) ON DELETE SET NULL,
                        claim_key TEXT NOT NULL UNIQUE,
                        video_url TEXT NOT NULL,
                        video_title TEXT,
                        channel_name TEXT,
                        expert_name TEXT,
                        published_at TEXT,
                        statement TEXT NOT NULL,
                        timestamp_text TEXT,
                        geographic_scope TEXT,
                        property_type TEXT,
                        expected_direction TEXT,
                        expected_period TEXT,
                        basis_json TEXT NOT NULL,
                        on_screen_evidence_json TEXT NOT NULL,
                        conditional INTEGER NOT NULL,
                        verification_needed_json TEXT NOT NULL,
                        counter_evidence_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS ingestion_jobs (
                        id TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        source_id TEXT NOT NULL REFERENCES source_registry(id) ON DELETE RESTRICT,
                        job_type TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,
                        region TEXT NOT NULL,
                        district_code TEXT,
                        source_url TEXT,
                        parameters_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        retryable INTEGER NOT NULL DEFAULT 1,
                        next_retry_at TEXT,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_jobs_runnable
                        ON ingestion_jobs(status, next_retry_at, created_at);

                    CREATE TABLE IF NOT EXISTS real_estate_reports (
                        id TEXT PRIMARY KEY,
                        watchlist_id TEXT REFERENCES real_estate_watchlists(id) ON DELETE SET NULL,
                        analysis_mode TEXT NOT NULL,
                        query TEXT NOT NULL,
                        region TEXT NOT NULL,
                        as_of_date TEXT NOT NULL,
                        status TEXT NOT NULL,
                        evidence_pack_json TEXT NOT NULL,
                        report_json TEXT NOT NULL,
                        review_json TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_reports_watchlist_created
                        ON real_estate_reports(watchlist_id, created_at DESC);
                    """
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (MIGRATION_VERSION, utc_now()),
                )
            self._initialized = True

    def upsert_source(self, source: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_registry(
                    id, name, source_type, base_url, publisher, reliability_level,
                    status, configuration_env, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, source_type=excluded.source_type,
                    base_url=excluded.base_url, publisher=excluded.publisher,
                    reliability_level=excluded.reliability_level,
                    status=CASE WHEN source_registry.status IN ('temporarily_unavailable','error')
                                THEN source_registry.status ELSE excluded.status END,
                    configuration_env=excluded.configuration_env,
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (
                    source["id"], source["name"], source["source_type"], source["base_url"],
                    source["publisher"], source["reliability_level"], source["status"],
                    source.get("configuration_env"), int(bool(source.get("enabled", True))), now, now,
                ),
            )
        return self.get_source(str(source["id"]))

    def list_sources(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_registry ORDER BY reliability_level, name"
            ).fetchall()
        return [_source_row(row) for row in rows]

    def get_source(self, source_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM source_registry WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError("Real estate source not found")
        return _source_row(row)

    def source_success(self, source_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE source_registry SET status='ready', last_success_at=?, last_error=NULL, failure_count=0, next_retry_at=NULL, updated_at=? WHERE id=?",
                (now, now, source_id),
            )

    def source_failure(
        self,
        source_id: str,
        *,
        message: str,
        retryable: bool,
        next_retry_at: str | None,
        status: str | None = None,
    ) -> None:
        source_status = status or ("temporarily_unavailable" if retryable else "error")
        if source_status not in {
            "ready", "configuration_required", "temporarily_unavailable", "disabled", "error"
        }:
            source_status = "error"
        with self._connect() as connection:
            connection.execute(
                "UPDATE source_registry SET status=?, last_error=?, failure_count=failure_count+1, next_retry_at=?, updated_at=? WHERE id=?",
                (source_status, message[:500], next_retry_at, utc_now(), source_id),
            )

    def save_fetch_result(self, result: FetchResult) -> dict[str, int]:
        self.initialize()
        counts = {"source_items": 0, "transactions": 0, "indicators": 0, "policies": 0}
        if not result.items:
            return counts
        with self._connect() as connection:
            item_ids: list[int] = []
            for item in result.items:
                item_id, created = self._save_source_item(connection, item)
                item_ids.append(item_id)
                counts["source_items"] += int(created)
            source_item_id = item_ids[0]
            for transaction in result.transactions:
                counts["transactions"] += self._save_transaction(connection, source_item_id, transaction)
            for indicator in result.indicators:
                counts["indicators"] += self._save_indicator(connection, source_item_id, indicator)
            for policy in result.policies:
                counts["policies"] += self._save_policy(connection, source_item_id, policy)
        return counts

    def list_source_items(
        self, *, source_id: str | None = None, region: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        if region:
            clauses.append("geographic_scope=?")
            params.append(region)
        query = "SELECT * FROM raw_source_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY retrieved_at DESC, id DESC LIMIT ?"
        params.append(min(max(limit, 1), 200))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_source_item_row(row, include_raw=False) for row in rows]

    def get_source_item(self, item_id: int) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM raw_source_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError("Evidence source item not found")
        return _source_item_row(row, include_raw=True)

    def list_transactions(
        self,
        *,
        region: str,
        period_start: str | None = None,
        period_end: str | None = None,
        apartment_name: str | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        self.initialize()
        clauses = ["region=?"]
        params: list[Any] = [region]
        if period_start:
            clauses.append("deal_date>=?")
            params.append(period_start)
        if period_end:
            clauses.append("deal_date<=?")
            params.append(period_end)
        if apartment_name:
            clauses.append("apartment_name=?")
            params.append(apartment_name)
        params.append(min(max(limit, 1), 50_000))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM real_estate_transactions WHERE " + " AND ".join(clauses) + " ORDER BY deal_date, id LIMIT ?",
                params,
            ).fetchall()
        return [_transaction_row(row) for row in rows]

    def list_indicators(self, *, region: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        self.initialize()
        query = "SELECT * FROM normalized_indicators"
        params: list[Any] = []
        if region:
            query += " WHERE region=?"
            params.append(region)
        query += " ORDER BY period DESC, id DESC LIMIT ?"
        params.append(min(max(limit, 1), 5000))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_json_columns(dict(row), {"metadata_json": "metadata"}) for row in rows]

    def create_watchlist(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        watchlist_id = str(uuid.uuid4())
        now = utc_now()
        values = _watchlist_values(payload)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO real_estate_watchlists(id,name,regions_json,complexes_json,comparison_regions_json,property_types_json,district_codes_json,profile_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (watchlist_id, *values, now, now),
            )
        return self.get_watchlist(watchlist_id)

    def list_watchlists(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM real_estate_watchlists ORDER BY updated_at DESC").fetchall()
        return [_watchlist_row(row) for row in rows]

    def get_watchlist(self, watchlist_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM real_estate_watchlists WHERE id=?", (watchlist_id,)).fetchone()
        if row is None:
            raise KeyError("Watchlist not found")
        return _watchlist_row(row)

    def update_watchlist(self, watchlist_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.get_watchlist(watchlist_id)
        values = _watchlist_values(payload)
        with self._connect() as connection:
            connection.execute(
                "UPDATE real_estate_watchlists SET name=?,regions_json=?,complexes_json=?,comparison_regions_json=?,property_types_json=?,district_codes_json=?,profile_json=?,updated_at=? WHERE id=?",
                (*values, utc_now(), watchlist_id),
            )
        return self.get_watchlist(watchlist_id)

    def delete_watchlist(self, watchlist_id: str) -> None:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM real_estate_watchlists WHERE id=?", (watchlist_id,))
            if cursor.rowcount == 0:
                raise KeyError("Watchlist not found")

    def list_policies(self, *, region: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM policy_events ORDER BY last_verified_at DESC, id DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        policies = [_policy_row(row) for row in rows]
        if region:
            policies = [item for item in policies if not item["affected_regions"] or region in item["affected_regions"]]
        return policies

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        source_id = str(payload["source_id"])
        self.get_source(source_id)
        key = stable_key(
            source_id,
            payload.get("job_type", "manual"),
            payload["period_start"], payload["period_end"], payload.get("region"),
            payload.get("district_code"), payload.get("source_url"), payload.get("parameters") or {},
        )
        now = utc_now()
        job_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO ingestion_jobs(id,idempotency_key,source_id,job_type,period_start,period_end,region,district_code,source_url,parameters_json,status,attempts,max_attempts,retryable,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, key, source_id, str(payload.get("job_type") or "manual")[:30],
                    str(payload["period_start"])[:20], str(payload["period_end"])[:20],
                    str(payload.get("region") or "대한민국")[:100],
                    str(payload.get("district_code") or "")[:20] or None,
                    str(payload.get("source_url") or "")[:2000] or None,
                    json.dumps(payload.get("parameters") or {}, ensure_ascii=False),
                    "queued", 0, min(max(int(payload.get("max_attempts", 3)), 1), 5), 1, now,
                ),
            )
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE idempotency_key=?", (key,)).fetchone()
        return _job_row(row)

    def claim_next_job(self) -> dict[str, Any] | None:
        self.initialize()
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM ingestion_jobs WHERE status IN ('queued','retry') AND (next_retry_at IS NULL OR next_retry_at<=?) ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE ingestion_jobs SET status='running', attempts=attempts+1, started_at=?, last_error=NULL WHERE id=?",
                (now, row["id"]),
            )
        return self.get_job(str(row["id"]))

    def complete_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ingestion_jobs SET status='completed', retryable=0, next_retry_at=NULL, finished_at=? WHERE id=?",
                (utc_now(), job_id),
            )
        return self.get_job(job_id)

    def fail_job(self, job_id: str, *, message: str, retryable: bool, base_delay_seconds: int = 30) -> dict[str, Any]:
        job = self.get_job(job_id)
        should_retry = retryable and int(job["attempts"]) < int(job["max_attempts"])
        next_retry_at = None
        if should_retry:
            delay = base_delay_seconds * (2 ** max(int(job["attempts"]) - 1, 0))
            next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE ingestion_jobs SET status=?, retryable=?, next_retry_at=?, last_error=?, finished_at=? WHERE id=?",
                (
                    "retry" if should_retry else "failed", int(retryable), next_retry_at,
                    message[:500], None if should_retry else utc_now(), job_id,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Ingestion job not found")
        return _job_row(row)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [_job_row(row) for row in rows]

    def save_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        report_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO real_estate_reports(id,watchlist_id,analysis_mode,query,region,as_of_date,status,evidence_pack_json,report_json,review_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report_id, payload.get("watchlist_id"), payload["analysis_mode"],
                    str(payload["query"])[:20_000], str(payload["region"])[:100],
                    payload["as_of_date"], payload["status"],
                    json.dumps(payload.get("evidence_pack") or {}, ensure_ascii=False),
                    json.dumps(payload.get("report") or {}, ensure_ascii=False),
                    json.dumps(payload.get("review"), ensure_ascii=False) if payload.get("review") else None,
                    utc_now(),
                ),
            )
        return self.get_report(report_id)

    def get_report(self, report_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM real_estate_reports WHERE id=?", (report_id,)).fetchone()
        if row is None:
            raise KeyError("Real estate report not found")
        return _report_row(row)

    def latest_report(self, watchlist_id: str | None = None) -> dict[str, Any] | None:
        self.initialize()
        query = "SELECT * FROM real_estate_reports"
        params: tuple[Any, ...] = ()
        if watchlist_id:
            query += " WHERE watchlist_id=?"
            params = (watchlist_id,)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return _report_row(row) if row else None

    def _save_source_item(self, connection: sqlite3.Connection, item: NormalizedSourceItem) -> tuple[int, bool]:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO raw_source_items(source_id,idempotency_key,source_name,source_type,source_url,publisher,published_at,retrieved_at,event_date,period,geographic_scope,property_type,reliability_level,raw_reference,structured_json,caveats_json,content_hash,raw_payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.source_id, item.idempotency_key, item.source_name, item.source_type,
                item.source_url, item.publisher, item.published_at, item.retrieved_at,
                item.event_date, item.period, item.geographic_scope, item.property_type,
                item.reliability_level, item.raw_reference,
                json.dumps(item.structured_values, ensure_ascii=False),
                json.dumps(item.caveats, ensure_ascii=False), item.content_hash, item.raw_payload,
            ),
        )
        row = connection.execute(
            "SELECT id FROM raw_source_items WHERE idempotency_key=?", (item.idempotency_key,)
        ).fetchone()
        return int(row["id"]), cursor.rowcount > 0

    @staticmethod
    def _save_transaction(connection: sqlite3.Connection, source_item_id: int, item: dict[str, Any]) -> int:
        now = utc_now()
        existing = connection.execute(
            "SELECT id FROM real_estate_transactions WHERE transaction_key=?",
            (item["transaction_key"],),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO real_estate_transactions(source_item_id,transaction_key,region,district_code,apartment_name,exclusive_area_sqm,deal_amount_krw,deal_date,floor,built_year,cancelled_at,correction_type,raw_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(transaction_key) DO UPDATE SET
                source_item_id=excluded.source_item_id, deal_amount_krw=excluded.deal_amount_krw,
                cancelled_at=excluded.cancelled_at, correction_type=excluded.correction_type,
                raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """,
            (
                source_item_id, item["transaction_key"], item["region"], item.get("district_code"),
                item.get("apartment_name") or "", item.get("exclusive_area_sqm"),
                item.get("deal_amount_krw"), item.get("deal_date"), item.get("floor"),
                item.get("built_year"), item.get("cancelled_at"), item.get("correction_type"),
                json.dumps(item.get("raw") or {}, ensure_ascii=False), now, now,
            ),
        )
        return int(existing is None)

    @staticmethod
    def _save_indicator(connection: sqlite3.Connection, source_item_id: int, item: dict[str, Any]) -> int:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO normalized_indicators(source_item_id,indicator_type,region,property_type,period,value,unit,sample_count,status,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                source_item_id, item["indicator_type"], item["region"], item.get("property_type"),
                item["period"], item["value"], item["unit"], item.get("sample_count"),
                item.get("status") or "sufficient", json.dumps(item.get("metadata") or {}, ensure_ascii=False),
            ),
        )
        return int(cursor.rowcount > 0)

    @staticmethod
    def _save_policy(connection: sqlite3.Connection, source_item_id: int, item: dict[str, Any]) -> int:
        event_key = stable_key(item["official_source"], item["policy_name"], item.get("status"), item.get("announced_at"))
        cursor = connection.execute(
            "INSERT OR IGNORE INTO policy_events(source_item_id,event_key,policy_name,policy_type,status,announced_at,passed_at,effective_at,affected_regions_json,affected_property_types_json,official_source,expected_transmission_path,counter_effects_json,confidence,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_item_id, event_key, item["policy_name"], item["policy_type"], item["status"],
                item.get("announced_at"), item.get("passed_at"), item.get("effective_at"),
                json.dumps(item.get("affected_regions") or [], ensure_ascii=False),
                json.dumps(item.get("affected_property_types") or [], ensure_ascii=False),
                item["official_source"], item.get("expected_transmission_path"),
                json.dumps(item.get("counter_effects") or [], ensure_ascii=False),
                item.get("confidence") or "medium", item.get("last_verified_at") or utc_now(),
            ),
        )
        return int(cursor.rowcount > 0)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _watchlist_values(payload: dict[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    name = " ".join(str(payload.get("name") or "Watchlist").split())[:100]
    return (
        name,
        json.dumps(_string_list(payload.get("regions")), ensure_ascii=False),
        json.dumps(_string_list(payload.get("complexes")), ensure_ascii=False),
        json.dumps(_string_list(payload.get("comparison_regions")), ensure_ascii=False),
        json.dumps(_string_list(payload.get("property_types")) or ["apartment"], ensure_ascii=False),
        json.dumps(_string_map(payload.get("district_codes")), ensure_ascii=False),
        json.dumps(_profile(payload.get("profile")), ensure_ascii=False),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(" ".join(str(item).split())[:100] for item in value if str(item).strip()))[:50]


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key)[:100]: str(item)[:20] for key, item in list(value.items())[:50] if str(key).strip() and str(item).strip()}


def _profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "holding_period", "target_sale_date", "residential_purpose", "risk_tolerance",
        "overseas_move_plan", "available_investment_band", "loan_band", "cash_band",
    }
    return {key: value[key] for key in allowed if key in value and value[key] not in {None, ""}}


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    return result


def _watchlist_row(row: sqlite3.Row) -> dict[str, Any]:
    return _json_columns(
        dict(row),
        {
            "regions_json": "regions", "complexes_json": "complexes",
            "comparison_regions_json": "comparison_regions",
            "property_types_json": "property_types", "district_codes_json": "district_codes",
            "profile_json": "profile",
        },
    )


def _source_item_row(row: sqlite3.Row, *, include_raw: bool) -> dict[str, Any]:
    result = _json_columns(dict(row), {"structured_json": "structured_values", "caveats_json": "caveats"})
    if not include_raw:
        result.pop("raw_payload", None)
    return result


def _transaction_row(row: sqlite3.Row) -> dict[str, Any]:
    return _json_columns(dict(row), {"raw_json": "raw"})


def _policy_row(row: sqlite3.Row) -> dict[str, Any]:
    return _json_columns(
        dict(row),
        {
            "affected_regions_json": "affected_regions",
            "affected_property_types_json": "affected_property_types",
            "counter_effects_json": "counter_effects",
        },
    )


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _json_columns(dict(row), {"parameters_json": "parameters"})
    result["retryable"] = bool(result["retryable"])
    return result


def _report_row(row: sqlite3.Row) -> dict[str, Any]:
    return _json_columns(
        dict(row),
        {"evidence_pack_json": "evidence_pack", "report_json": "report", "review_json": "review"},
    )


def _json_columns(result: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    for old, new in mapping.items():
        raw = result.pop(old, None)
        try:
            result[new] = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            result[new] = None
    return result
