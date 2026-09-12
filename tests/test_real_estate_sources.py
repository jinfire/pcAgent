from pathlib import Path

import pytest

from app.real_estate.models import FetchRequest, SourceAdapterError
from app.real_estate.policy import classify_policy_status
from app.real_estate.sources import (
    MolitApartmentTradeAdapter,
    OfficialPolicyAdapter,
    RoneIndexAdapter,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureHttpClient:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.periods = []

    def get(self, url, *, params, allowed_hosts):
        del url, allowed_hosts
        self.periods.append(params["DEAL_YMD"])
        return self.payload, "application/xml"


def request(**overrides):
    values = {
        "period_start": "2026-08",
        "period_end": "2026-08",
        "region": "동탄",
        "district_code": "41590",
        "parameters": {},
    }
    values.update(overrides)
    return FetchRequest(**values)


def test_molit_fixture_parsing_and_api_key_requirement() -> None:
    adapter = MolitApartmentTradeAdapter("")
    with pytest.raises(SourceAdapterError) as error:
        adapter.fetch(request())
    assert error.value.category == "configuration_required"
    assert error.value.retryable is False

    result = adapter.parse((FIXTURES / "molit_transactions.xml").read_bytes(), request())
    assert len(result.transactions) == 3
    assert result.transactions[0]["deal_amount_krw"] == 1_000_000_000
    assert result.transactions[2]["cancelled_at"] == "2026-08-25"
    assert result.items[0].reliability_level == "A"
    assert "serviceKey" not in result.items[0].source_url


def test_molit_fetch_collects_each_requested_month() -> None:
    http = FixtureHttpClient((FIXTURES / "molit_transactions.xml").read_bytes())
    result = MolitApartmentTradeAdapter("configured", http).fetch(
        request(period_start="2026-07", period_end="2026-08")
    )
    assert http.periods == ["202607", "202608"]
    assert [item.period for item in result.items] == ["2026-07", "2026-08"]


def test_rone_fixture_parsing_and_api_key_requirement() -> None:
    adapter = RoneIndexAdapter("")
    with pytest.raises(SourceAdapterError):
        adapter.fetch(request(parameters={"statbl_id": "A_TEST"}))
    result = adapter.parse((FIXTURES / "rone_index.json").read_bytes(), request())
    assert [item["value"] for item in result.indicators] == [101.2, 101.8]
    assert result.indicators[0]["region"] == "동탄"


def test_policy_state_does_not_promote_pledge_or_review_to_effective() -> None:
    assert classify_policy_status("후보자가 공급 확대를 공약했다") == "pledge"
    assert classify_policy_status("정부가 LTV 변경을 검토 중이다") == "under_review"
    assert classify_policy_status("개정 법률이 공포되었다") == "promulgated"
    assert classify_policy_status("제도는 10월부터 시행한다") == "effective"


def test_official_policy_parser_keeps_external_content_untrusted() -> None:
    adapter = OfficialPolicyAdapter()
    result = adapter.parse(
        (FIXTURES / "policy_document.html").read_bytes(),
        request(source_url="https://www.fsc.go.kr/example", district_code=None),
    )
    assert result.policies[0]["status"] == "under_review"
    assert result.policies[0]["official_source"].startswith("https://www.fsc.go.kr/")
    assert "stealCredentials" not in result.items[0].raw_payload
    assert "Ignore previous instructions" in result.items[0].raw_payload
