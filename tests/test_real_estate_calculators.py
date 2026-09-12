from app.real_estate.calculators import calculate_market_indicators


def transaction(month: int, amount: int, *, cancelled: bool = False) -> dict:
    return {
        "deal_date": f"2025-{month:02d}-10",
        "deal_amount_krw": amount,
        "exclusive_area_sqm": 100,
        "cancelled_at": "2025-12-01" if cancelled else None,
    }


def test_market_calculations_use_median_and_report_samples() -> None:
    rows = [
        transaction(1, 100_000_000),
        transaction(1, 110_000_000),
        transaction(1, 900_000_000),
        transaction(2, 120_000_000),
        transaction(2, 130_000_000),
        transaction(2, 140_000_000),
        transaction(2, 150_000_000, cancelled=True),
    ]
    result = calculate_market_indicators(rows)
    assert result["sample_count"] == 6
    assert result["cancelled_or_corrected_count"] == 1
    assert result["monthly"][0]["median_deal_amount_krw"] == 110_000_000
    assert result["latest"]["sample_count"] == 3
    assert result["latest"]["status"] == "sufficient"
    assert result["latest"]["volume_mom_pct"] == 0.0


def test_small_sample_is_never_presented_as_a_trend() -> None:
    result = calculate_market_indicators([transaction(1, 100_000_000)])
    assert result["status"] == "insufficient_sample"
    assert result["latest"]["sample_count"] == 1
