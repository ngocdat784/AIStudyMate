from __future__ import annotations

from typing import Any

import pandas as pd

from app.services.manufacturing_data_foundation import _resolve_mapping


_FIELD_GROUPS: dict[str, set[str]] = {
    "production": {"Production", "Planned Production"},
    "quality": {
        "Defects",
        "Defect",
        "Defective Units",
        "Inspected Quantity",
        "First Pass Inspected",
        "First Pass Passed",
        "Reworked Units",
        "Scrapped Units",
        "Quality Status",
        "Measurement Value",
        "Lower Spec Limit",
        "Upper Spec Limit",
    },
    "machine": {
        "Machine",
        "Machine Status",
        "Downtime",
        "Downtime Type",
        "Downtime Planned",
        "Maintenance Action",
        "Maintenance Outcome",
        "Cycle Time",
        "Standard Cycle Time",
        "Available Time",
        "Operating Time",
        "Waiting Time",
    },
    "material": {
        "Material",
        "Material Type",
        "Material Lot",
        "Material Stock",
        "Planned Consumption",
        "Actual Consumption",
        "Material Unit Cost",
        "Material Cost",
        "Material Loss",
        "Scrap Material",
    },
}

_ANALYSIS_REQUIREMENTS: dict[str, tuple[str, set[str]]] = {
    "production": ("sản xuất", {"Production"}),
    "quality": (
        "chất lượng",
        {
            "Defects",
            "Defective Units",
            "Inspected Quantity",
            "First Pass Inspected",
            "First Pass Passed",
            "Reworked Units",
            "Scrapped Units",
            "Quality Status",
            "Measurement Value",
        },
    ),
    "machine": (
        "máy móc",
        {
            "Machine Status",
            "Downtime",
            "Downtime Type",
            "Downtime Planned",
            "Maintenance Action",
            "Maintenance Outcome",
            "Cycle Time",
            "Standard Cycle Time",
            "Available Time",
            "Operating Time",
            "Waiting Time",
        },
    ),
    "material": (
        "vật liệu",
        {
            "Material Stock",
            "Planned Consumption",
            "Actual Consumption",
            "Material Unit Cost",
            "Material Cost",
            "Material Loss",
            "Scrap Material",
        },
    ),
    "intelligence": (
        "Manufacturing Intelligence",
        {"Production", "Defects", "Downtime"},
    ),
}

_GENERAL_FIELDS = set().union(
    *(fields for _, fields in _ANALYSIS_REQUIREMENTS.values())
)
_QUALITY_FIELDS = _ANALYSIS_REQUIREMENTS["quality"][1]
_PREDICTIVE_METRICS = {
    "Production",
    "Defects",
    "Downtime",
    "Actual Consumption",
    "Material Stock",
    "Measurement Value",
}


def classify_analysis_dataset(
    dataframe: pd.DataFrame,
    *,
    column_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    mapping = _resolve_mapping(list(dataframe.columns), column_mapping)
    fields = sorted(set(mapping.values()))
    field_set = set(fields)
    domains = [
        domain
        for domain, domain_fields in _FIELD_GROUPS.items()
        if field_set.intersection(domain_fields)
    ]

    analyses: dict[str, dict[str, Any]] = {}
    for analysis, (label, required_fields) in _ANALYSIS_REQUIREMENTS.items():
        matched_fields = sorted(field_set.intersection(required_fields))
        analyses[analysis] = {
            "label": label,
            "ready": bool(matched_fields),
            "matched_fields": matched_fields,
            "required_any_of": sorted(required_fields),
        }

    analyses["general"] = {
        "label": "phân tích file tổng quát",
        "ready": bool(field_set.intersection(_GENERAL_FIELDS)),
        "matched_fields": sorted(field_set.intersection(_GENERAL_FIELDS)),
        "required_any_of": sorted(_GENERAL_FIELDS),
    }

    has_production = "Production" in field_set
    has_quality = bool(field_set.intersection(_QUALITY_FIELDS))
    for analysis, label in (
        ("root_cause", "nguyên nhân gốc"),
        ("system_integration", "tích hợp hệ thống"),
    ):
        analyses[analysis] = {
            "label": label,
            "ready": has_production and has_quality,
            "matched_fields": sorted(
                field_set.intersection({"Production"} | _QUALITY_FIELDS)
            ),
            "required_all": ["Production"],
            "required_any_of": sorted(_QUALITY_FIELDS),
        }

    date_present = "Date" in field_set
    predictive_metrics = sorted(field_set.intersection(_PREDICTIVE_METRICS))
    analyses["predictive"] = {
        "label": "dự báo",
        "ready": date_present and bool(predictive_metrics),
        "matched_fields": (
            (["Date"] if date_present else []) + predictive_metrics
        ),
        "required_all": ["Date"],
        "required_any_of": sorted(_PREDICTIVE_METRICS),
    }

    return {
        "domain": domains[0] if len(domains) == 1 else (
            "mixed" if domains else "unrecognized"
        ),
        "domains": domains,
        "recognized_fields": fields,
        "analyses": analyses,
    }


def ensure_analysis_ready(
    classification: dict[str, Any],
    analysis: str,
) -> None:
    readiness = classification["analyses"].get(analysis)
    if readiness is None:
        raise ValueError(f"Không hỗ trợ kiểm tra sẵn sàng cho '{analysis}'.")
    if readiness["ready"]:
        return

    recognized_fields = classification["recognized_fields"]
    fields_text = ", ".join(recognized_fields) if recognized_fields else "không có"
    if analysis == "predictive":
        required_text = "cột Date và ít nhất một chỉ số có thể dự báo"
    elif "required_all" in readiness:
        required_text = (
            "cột "
            + ", ".join(readiness["required_all"])
            + " và ít nhất một trong các trường: "
            + ", ".join(readiness["required_any_of"])
        )
    else:
        required_text = (
            "ít nhất một trong các trường: "
            + ", ".join(readiness["required_any_of"])
        )
    raise ValueError(
        f"File chưa đủ dữ liệu cho phân tích {readiness['label']}. "
        f"Đã nhận diện: {fields_text}. Cần {required_text}."
    )
