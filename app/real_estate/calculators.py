from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median
from typing import Any


MIN_TREND_SAMPLE = 3


def calculate_market_indicators(
    transactions: list[dict[str, Any]], *, min_sample: int = MIN_TREND_SAMPLE
) -> dict[str, Any]:
    """Calculate repeatable monthly indicators without delegating arithmetic to an LLM."""
    valid = [item for item in transactions if not item.get("cancelled_at")]
    cancelled_count = len(transactions) - len(valid)
    monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in valid:
        deal_date = str(item.get("deal_date") or "")
        amount = _number(item.get("deal_amount_krw"))
        area = _number(item.get("exclusive_area_sqm"))
        if len(deal_date) < 7 or amount is None or area is None or area <= 0:
            continue
        enriched = dict(item)
        enriched["price_per_sqm_krw"] = round(amount / area)
        monthly[deal_date[:7]].append(enriched)

    rows: list[dict[str, Any]] = []
    periods = sorted(monthly)
    medians: list[float] = []
    for index, period in enumerate(periods):
        values = monthly[period]
        prices = [float(item["price_per_sqm_krw"]) for item in values]
        amount_values = [float(item["deal_amount_krw"]) for item in values]
        medians.append(float(median(prices)))
        rolling_values = medians[max(0, index - 2) : index + 1]
        rows.append(
            {
                "period": period,
                "sample_count": len(values),
                "status": "sufficient" if len(values) >= min_sample else "insufficient_sample",
                "median_price_per_sqm_krw": round(median(prices)),
                "median_deal_amount_krw": round(median(amount_values)),
                "rolling_3m_median_price_per_sqm_krw": round(median(rolling_values)),
                "transaction_volume": len(values),
            }
        )

    for index, row in enumerate(rows):
        row["volume_mom_pct"] = _pct_change(row["transaction_volume"], rows[index - 1]["transaction_volume"]) if index >= 1 else None
        row["volume_yoy_pct"] = _pct_change(row["transaction_volume"], rows[index - 12]["transaction_volume"]) if index >= 12 else None
        row["price_change_6m_pct"] = _pct_change(
            row["rolling_3m_median_price_per_sqm_krw"],
            rows[index - 6]["rolling_3m_median_price_per_sqm_krw"],
        ) if index >= 6 else None
        row["price_change_12m_pct"] = _pct_change(
            row["rolling_3m_median_price_per_sqm_krw"],
            rows[index - 12]["rolling_3m_median_price_per_sqm_krw"],
        ) if index >= 12 else None

    latest = rows[-1] if rows else None
    peak = max((row["rolling_3m_median_price_per_sqm_krw"] for row in rows), default=None)
    peak_drawdown = (
        _pct_change(latest["rolling_3m_median_price_per_sqm_krw"], peak)
        if latest and peak
        else None
    )
    return {
        "as_of_date": max((str(item.get("deal_date") or "") for item in valid), default=None),
        "sample_count": len(valid),
        "cancelled_or_corrected_count": cancelled_count,
        "status": latest["status"] if latest else "insufficient_sample",
        "latest": latest,
        "monthly": rows,
        "recent_peak_drawdown_pct": peak_drawdown,
        "caveats": [
            "최근 거래는 신고 지연과 취소·정정으로 수치가 바뀔 수 있습니다.",
            "월 표본이 적으면 추세로 단정하지 않습니다.",
            "이상치 영향을 줄이기 위해 평균보다 중앙값을 우선 사용했습니다.",
        ],
    }


def calculate_comparison_ratio(primary: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    first = _latest_value(primary)
    second = _latest_value(comparison)
    if first is None or second in {None, 0}:
        return {"ratio": None, "status": "insufficient_sample"}
    enough = primary.get("status") == "sufficient" and comparison.get("status") == "sufficient"
    return {
        "ratio": round(first / second, 4),
        "status": "sufficient" if enough else "insufficient_sample",
        "unit": "ratio",
    }


def calculate_jeonse_ratio(sale_price_krw: object, jeonse_price_krw: object) -> float | None:
    sale = _number(sale_price_krw)
    jeonse = _number(jeonse_price_krw)
    if sale is None or sale <= 0 or jeonse is None:
        return None
    return round(jeonse / sale * 100, 2)


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = _parse_month(start)
    end_year, end_month = _parse_month(end)
    if (start_year, start_month) > (end_year, end_month):
        raise ValueError("period_start must be before period_end")
    result: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
        if len(result) > 120:
            raise ValueError("Collection period cannot exceed 120 months")
    return result


def _latest_value(result: dict[str, Any]) -> float | None:
    latest = result.get("latest") or {}
    return _number(latest.get("rolling_3m_median_price_per_sqm_krw"))


def _pct_change(current: object, previous: object) -> float | None:
    current_value = _number(current)
    previous_value = _number(previous)
    if current_value is None or previous_value in {None, 0}:
        return None
    return round((current_value / previous_value - 1) * 100, 2)


def _number(value: object) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _parse_month(value: str) -> tuple[int, int]:
    cleaned = value.strip()[:7]
    try:
        parsed = date.fromisoformat(f"{cleaned}-01")
    except ValueError as exc:
        raise ValueError("Periods must use YYYY-MM format") from exc
    return parsed.year, parsed.month
