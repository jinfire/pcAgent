from __future__ import annotations

import re

from app.real_estate.models import POLICY_STATUSES


def classify_policy_status(text: str) -> str:
    """Classify explicit process state; never promote a pledge to an enacted policy."""
    normalized = " ".join(text.casefold().split())
    rules = (
        ("repealed", ("폐지", "효력 상실", "repealed")),
        ("effective", ("시행한다", "시행 중", "시행일", "effective")),
        ("promulgated", ("공포", "관보 게재", "promulgated")),
        ("bill_passed", ("본회의 통과", "국회 통과", "가결", "bill passed")),
        ("bill_proposed", ("법안 발의", "개정안 발의", "의안 발의", "bill proposed")),
        ("under_review", ("검토 중", "논의 중", "추진 검토", "under review")),
        ("pledge", ("공약", "약속", "pledge")),
        ("official_announcement", ("공식 발표", "보도자료", "고시", "공고")),
    )
    for status, markers in rules:
        if any(marker in normalized for marker in markers):
            return status
    return "statement"


def validate_policy_status(value: str) -> str:
    status = value.strip().casefold()
    if status not in POLICY_STATUSES:
        raise ValueError("Invalid policy status")
    return status


def infer_policy_type(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.casefold())
    categories = {
        "ltv": ("ltv", "주택담보인정비율"),
        "dsr": ("dsr", "총부채원리금상환비율"),
        "acquisition_tax": ("취득세",),
        "holding_tax": ("보유세", "종합부동산세", "재산세"),
        "capital_gains_tax": ("양도소득세", "양도세"),
        "redevelopment": ("재건축", "재개발", "정비사업"),
        "price_cap": ("분양가상한제",),
        "housing_supply": ("주택 공급", "공급 대책", "입주 물량"),
        "transport_development": ("교통", "철도", "gtx", "개발사업"),
        "rental_system": ("임대차", "전월세"),
        "regulated_area": ("규제지역", "조정대상지역", "투기과열지구"),
        "land_transaction_permit": ("토지거래허가",),
        "corporate_foreign": ("외국인", "법인 규제"),
    }
    for category, markers in categories.items():
        if any(marker in normalized for marker in markers):
            return category
    return "other"
