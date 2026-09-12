from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.real_estate.models import (
    FetchRequest,
    FetchResult,
    NormalizedSourceItem,
    SourceAdapterError,
    content_hash,
    stable_key,
    utc_now,
)
from app.real_estate.calculators import month_range
from app.real_estate.policy import classify_policy_status, infer_policy_type
from app.tools.general_tools import GeneralTools


MOLIT_ENDPOINT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
RONE_ENDPOINT = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
OFFICIAL_POLICY_HOSTS = {
    "molit.go.kr",
    "moef.go.kr",
    "fsc.go.kr",
    "fss.or.kr",
    "korea.kr",
    "assembly.go.kr",
    "likms.assembly.go.kr",
    "seoul.go.kr",
    "gg.go.kr",
    "hscity.go.kr",
    "eum.go.kr",
    "cleanup.seoul.go.kr",
    "apply.lh.or.kr",
    "lh.or.kr",
}


class SourceAdapter(ABC):
    source_id: str
    source_name: str
    source_type: str
    publisher: str
    base_url: str
    reliability_level: str = "A"
    configuration_env: str | None = None

    @property
    @abstractmethod
    def configured(self) -> bool:
        raise NotImplementedError

    @property
    def status(self) -> str:
        return "ready" if self.configured else "configuration_required"

    @abstractmethod
    def fetch(self, request: FetchRequest) -> FetchResult:
        raise NotImplementedError

    def registry_record(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "name": self.source_name,
            "source_type": self.source_type,
            "base_url": self.base_url,
            "publisher": self.publisher,
            "reliability_level": self.reliability_level,
            "status": self.status,
            "configuration_env": self.configuration_env,
            "enabled": True,
        }


class OfficialHttpClient:
    def __init__(self, *, timeout_seconds: float = 20.0, max_bytes: int = 2_000_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        allowed_hosts: set[str],
    ) -> tuple[bytes, str]:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or not hostname:
            raise SourceAdapterError("Only absolute HTTPS source URLs are allowed", category="invalid_request", retryable=False)
        if not _host_allowed(hostname, allowed_hosts):
            raise SourceAdapterError("Source domain is not allowlisted", category="security", retryable=False)
        GeneralTools._assert_public_host(hostname)
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                with client.stream(
                    "GET",
                    url,
                    params=params,
                    headers={"User-Agent": "MyAgent-RealEstate/0.1"},
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise SourceAdapterError("Source redirects are not followed", category="redirect", retryable=False)
                    if response.status_code in {408, 425, 429} or response.status_code >= 500:
                        raise SourceAdapterError(
                            f"Source temporarily unavailable (HTTP {response.status_code})",
                            category="temporarily_unavailable",
                            retryable=True,
                        )
                    if response.status_code >= 400:
                        raise SourceAdapterError(
                            f"Source request failed (HTTP {response.status_code})",
                            category="request_error",
                            retryable=False,
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise SourceAdapterError("Source response is too large", category="response_too_large", retryable=False)
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers.get("content-type", "")
        except SourceAdapterError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise SourceAdapterError("Source connection failed", category="temporarily_unavailable", retryable=True) from exc


class MolitApartmentTradeAdapter(SourceAdapter):
    source_id = "molit_apartment_trade"
    source_name = "국토교통부 아파트 매매 실거래가"
    source_type = "transaction"
    publisher = "국토교통부"
    base_url = MOLIT_ENDPOINT
    reliability_level = "A"
    configuration_env = "MOLIT_API_KEY"

    def __init__(self, api_key: str, http_client: OfficialHttpClient | None = None) -> None:
        self.api_key = api_key.strip()
        self.http = http_client or OfficialHttpClient()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, request: FetchRequest) -> FetchResult:
        if not self.configured:
            raise SourceAdapterError("MOLIT_API_KEY is not configured", category="configuration_required", retryable=False)
        if not request.district_code or not re.fullmatch(r"\d{5}", request.district_code):
            raise SourceAdapterError("A 5-digit legal district code is required", category="invalid_request", retryable=False)
        items: list[NormalizedSourceItem] = []
        transactions: list[dict[str, Any]] = []
        for period in month_range(request.period_start, request.period_end):
            body, _ = self.http.get(
                self.base_url,
                params={
                    "serviceKey": self.api_key,
                    "LAWD_CD": request.district_code,
                    "DEAL_YMD": period.replace("-", ""),
                    "pageNo": 1,
                    "numOfRows": min(int(request.parameters.get("num_rows", 1000)), 5000),
                },
                allowed_hosts={"apis.data.go.kr"},
            )
            parsed = self.parse(
                body,
                FetchRequest(
                    period_start=period,
                    period_end=period,
                    region=request.region,
                    district_code=request.district_code,
                    parameters=request.parameters,
                ),
            )
            items.extend(parsed.items)
            transactions.extend(parsed.transactions)
        return FetchResult(items=items, transactions=transactions)

    def parse(self, payload: str | bytes, request: FetchRequest) -> FetchResult:
        raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise SourceAdapterError("Invalid MOLIT XML response", category="parse_error", retryable=False) from exc
        result_code = _xml_text(root, "resultCode")
        if result_code and result_code not in {"00", "000"}:
            message = _xml_text(root, "resultMsg") or "MOLIT API error"
            retryable = result_code in {"01", "04", "05", "22", "23"}
            raise SourceAdapterError(message, category="source_error", retryable=retryable)

        transactions: list[dict[str, Any]] = []
        for node in root.findall(".//item"):
            values = {child.tag: (child.text or "").strip() for child in list(node)}
            year = _first(values, "dealYear", "년")
            month = _first(values, "dealMonth", "월")
            day = _first(values, "dealDay", "일")
            deal_date = _date_parts(year, month, day)
            apartment = _first(values, "aptNm", "아파트") or ""
            area = _float(_first(values, "excluUseAr", "전용면적"))
            amount_10k = _int(_first(values, "dealAmount", "거래금액"))
            amount = amount_10k * 10_000 if amount_10k is not None else None
            floor = _int(_first(values, "floor", "층"))
            built_year = _int(_first(values, "buildYear", "건축년도"))
            cancelled_at = _normalize_date(_first(values, "cdealDay", "해제사유발생일"))
            registration_id = _first(values, "dealId", "거래ID", "serialNumber")
            transaction_key = registration_id or stable_key(
                request.district_code,
                apartment,
                deal_date,
                area,
                floor,
                built_year,
                _first(values, "jibun", "지번"),
            )
            transactions.append(
                {
                    "transaction_key": transaction_key,
                    "region": request.region,
                    "district_code": request.district_code,
                    "apartment_name": apartment,
                    "exclusive_area_sqm": area,
                    "deal_amount_krw": amount,
                    "deal_date": deal_date,
                    "floor": floor,
                    "built_year": built_year,
                    "cancelled_at": cancelled_at,
                    "correction_type": "cancelled" if cancelled_at else None,
                    "raw": values,
                }
            )

        digest = content_hash(raw)
        item = NormalizedSourceItem(
            source_id=self.source_id,
            source_name=self.source_name,
            source_type=self.source_type,
            source_url=self.base_url,
            publisher=self.publisher,
            published_at=None,
            retrieved_at=utc_now(),
            event_date=None,
            period=request.period_start[:7],
            geographic_scope=request.region,
            property_type="apartment",
            reliability_level="A",
            raw_reference=digest,
            structured_values={"transaction_count": len(transactions)},
            caveats=["신고 지연과 취소·정정으로 최근 수치가 변경될 수 있습니다."],
            content_hash=digest,
            raw_payload=raw,
        )
        return FetchResult(items=[item], transactions=transactions)


class RoneIndexAdapter(SourceAdapter):
    source_id = "rone_market_index"
    source_name = "한국부동산원 R-ONE 통계"
    source_type = "market_index"
    publisher = "한국부동산원"
    base_url = RONE_ENDPOINT
    reliability_level = "B"
    configuration_env = "RONE_API_KEY"

    def __init__(self, api_key: str, http_client: OfficialHttpClient | None = None) -> None:
        self.api_key = api_key.strip()
        self.http = http_client or OfficialHttpClient()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, request: FetchRequest) -> FetchResult:
        if not self.configured:
            raise SourceAdapterError("RONE_API_KEY is not configured", category="configuration_required", retryable=False)
        statbl_id = str(request.parameters.get("statbl_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", statbl_id):
            raise SourceAdapterError("A valid R-ONE statbl_id is required", category="invalid_request", retryable=False)
        params = {
            "KEY": self.api_key,
            "Type": "json",
            "STATBL_ID": statbl_id,
            "DTACYCLE_CD": str(request.parameters.get("cycle") or "MM")[:4],
            "START_WRTTIME": request.period_start[:7].replace("-", ""),
            "END_WRTTIME": request.period_end[:7].replace("-", ""),
            "pIndex": 1,
            "pSize": min(int(request.parameters.get("page_size", 1000)), 5000),
        }
        if request.parameters.get("class_id"):
            params["CLS_ID"] = str(request.parameters["class_id"])[:80]
        if request.parameters.get("item_id"):
            params["ITM_ID"] = str(request.parameters["item_id"])[:80]
        body, _ = self.http.get(self.base_url, params=params, allowed_hosts={"reb.or.kr", "www.reb.or.kr"})
        return self.parse(body, request)

    def parse(self, payload: str | bytes, request: FetchRequest) -> FetchResult:
        raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SourceAdapterError("Invalid R-ONE JSON response", category="parse_error", retryable=False) from exc
        if isinstance(data, dict) and any(key in data for key in ("ERROR", "error", "errMsg")):
            raise SourceAdapterError("R-ONE returned an error", category="source_error", retryable=False)
        rows = _find_record_list(data)
        indicators: list[dict[str, Any]] = []
        for row in rows:
            value = _float(_first(row, "DTA_VAL", "DTVAL_CO", "value", "VAL"))
            if value is None:
                continue
            indicators.append(
                {
                    "indicator_type": str(request.parameters.get("indicator_type") or _first(row, "ITM_NM", "STATBL_NM") or "rone_index")[:100],
                    "region": str(_first(row, "CLS_NM", "C1_NM", "region") or request.region)[:100],
                    "property_type": str(request.parameters.get("property_type") or "housing")[:100],
                    "period": str(_first(row, "WRTTIME_IDTFR_ID", "WRTTIME", "period") or request.period_start)[:20],
                    "value": value,
                    "unit": str(_first(row, "UNIT_NM", "unit") or "index")[:30],
                    "sample_count": None,
                    "status": "sufficient",
                    "metadata": row,
                }
            )
        digest = content_hash(raw)
        item = NormalizedSourceItem(
            source_id=self.source_id,
            source_name=self.source_name,
            source_type=self.source_type,
            source_url=self.base_url,
            publisher=self.publisher,
            published_at=None,
            retrieved_at=utc_now(),
            event_date=None,
            period=f"{request.period_start}/{request.period_end}",
            geographic_scope=request.region,
            property_type=str(request.parameters.get("property_type") or "housing"),
            reliability_level="B",
            raw_reference=digest,
            structured_values={"indicator_count": len(indicators)},
            caveats=["통계표 코드, 지역 코드, 단위와 기준시점을 함께 확인해야 합니다."],
            content_hash=digest,
            raw_payload=raw,
        )
        return FetchResult(items=[item], indicators=indicators)


class OfficialPolicyAdapter(SourceAdapter):
    source_id = "official_policy"
    source_name = "정부·지자체 공식 정책 문서"
    source_type = "policy_document"
    publisher = "정부·국회·지자체"
    base_url = "https://www.korea.kr/"
    reliability_level = "A"
    configuration_env = None

    def __init__(self, http_client: OfficialHttpClient | None = None) -> None:
        self.http = http_client or OfficialHttpClient()

    @property
    def configured(self) -> bool:
        return True

    def fetch(self, request: FetchRequest) -> FetchResult:
        if not request.source_url:
            raise SourceAdapterError("An official policy document URL is required", category="invalid_request", retryable=False)
        body, content_type = self.http.get(request.source_url, params=None, allowed_hosts=OFFICIAL_POLICY_HOSTS)
        return self.parse(body, request, content_type=content_type)

    def parse(
        self,
        payload: str | bytes,
        request: FetchRequest,
        *,
        content_type: str = "text/html",
    ) -> FetchResult:
        raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
        text, title = _document_text(raw, content_type)
        if not text.strip():
            raise SourceAdapterError("Official document has no readable text", category="parse_error", retryable=False)
        status = classify_policy_status(text)
        source_url = request.source_url or self.base_url
        publisher = _publisher_for_url(source_url)
        digest = content_hash(text)
        excerpt = text[:20_000]
        item = NormalizedSourceItem(
            source_id=self.source_id,
            source_name=title or self.source_name,
            source_type=self.source_type,
            source_url=source_url,
            publisher=publisher,
            published_at=None,
            retrieved_at=utc_now(),
            event_date=None,
            period=request.period_start,
            geographic_scope=request.region or "대한민국",
            property_type=None,
            reliability_level="A",
            raw_reference=digest,
            structured_values={"title": title, "policy_status": status, "excerpt": excerpt},
            caveats=["문서의 발표·발의·통과·공포·시행 상태를 서로 구분해야 합니다."],
            content_hash=digest,
            raw_payload=excerpt,
        )
        policy = {
            "policy_name": (title or excerpt[:120]).strip(),
            "policy_type": infer_policy_type(text),
            "status": status,
            "announced_at": request.parameters.get("announced_at"),
            "passed_at": request.parameters.get("passed_at"),
            "effective_at": request.parameters.get("effective_at"),
            "affected_regions": [request.region] if request.region else [],
            "affected_property_types": list(request.parameters.get("property_types") or []),
            "official_source": source_url,
            "expected_transmission_path": request.parameters.get("expected_transmission_path"),
            "counter_effects": list(request.parameters.get("counter_effects") or []),
            "confidence": "high" if status in {"promulgated", "effective", "repealed"} else "medium",
            "last_verified_at": utc_now(),
        }
        return FetchResult(items=[item], policies=[policy])


def build_adapters(
    *,
    molit_api_key: str,
    rone_api_key: str,
    timeout_seconds: float,
    max_bytes: int,
) -> dict[str, SourceAdapter]:
    http_client = OfficialHttpClient(timeout_seconds=timeout_seconds, max_bytes=max_bytes)
    adapters: list[SourceAdapter] = [
        MolitApartmentTradeAdapter(molit_api_key, http_client),
        RoneIndexAdapter(rone_api_key, http_client),
        OfficialPolicyAdapter(http_client),
    ]
    return {adapter.source_id: adapter for adapter in adapters}


def _host_allowed(hostname: str, allowed_hosts: set[str]) -> bool:
    return any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in allowed_hosts)


def _xml_text(root: ET.Element, tag: str) -> str | None:
    node = root.find(f".//{tag}")
    return (node.text or "").strip() if node is not None else None


def _first(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in values and values[key] not in {None, ""}:
            return values[key]
    return None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip()) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip()) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _date_parts(year: Any, month: Any, day: Any) -> str | None:
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return None


def _normalize_date(value: Any) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value)[:20]


def _find_record_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        preferred = ("SttsApiTblData", "row", "rows", "data", "result", "RESULT")
        for key in preferred:
            if key in value:
                found = _find_record_list(value[key])
                if found:
                    return found
        for nested in value.values():
            found = _find_record_list(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_record_list(nested)
            if found:
                return found
    return []


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)
            if self._in_title:
                self.title_parts.append(cleaned)


def _document_text(raw: str, content_type: str) -> tuple[str, str | None]:
    if "html" not in content_type.casefold() and "<html" not in raw[:500].casefold():
        text = " ".join(html.unescape(raw).split())
        return text, None
    parser = _TextExtractor()
    parser.feed(raw)
    return "\n".join(parser.parts), " ".join(parser.title_parts)[:300] or None


def _publisher_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold()
    publishers = {
        "molit.go.kr": "국토교통부",
        "moef.go.kr": "기획재정부",
        "fsc.go.kr": "금융위원회",
        "fss.or.kr": "금융감독원",
        "korea.kr": "대한민국 정책브리핑",
        "assembly.go.kr": "대한민국 국회",
        "seoul.go.kr": "서울특별시",
        "gg.go.kr": "경기도",
        "hscity.go.kr": "화성시",
    }
    for domain, publisher in publishers.items():
        if host == domain or host.endswith(f".{domain}"):
            return publisher
    return "공식 공공기관"
