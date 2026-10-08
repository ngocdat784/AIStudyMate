# app/services/hardware_ai_service.py

from typing import Any, Literal

from google import genai
from pydantic import BaseModel, Field

from app.config import settings


GEMINI_MODEL = "gemini-3.5-flash-lite"


# ============================================================
# PYDANTIC SCHEMAS
# ============================================================

class AnalysisSection(BaseModel):
    summary: str = Field(
        description="Tóm tắt ngắn gọn dựa hoàn toàn trên dữ liệu được cung cấp."
    )

    key_observations: list[str] = Field(
        default_factory=list,
        description="Các quan sát có thể chứng minh trực tiếp từ dữ liệu."
    )


class RiskArea(BaseModel):
    entity_type: Literal[
        "product",
        "model",
        "component",
        "supplier",
        "batch",
        "defect_type",
        "date"
    ]

    entity_name: str

    risk_level: Literal[
        "low",
        "medium",
        "high",
        "critical"
    ]

    evidence_keys: list[
        Literal[
            "production",
            "defects",
            "defect_rate",
            "downtime"
        ]
    ] = Field(default_factory=list)

    observation: str

    investigation_points: list[str] = Field(
        default_factory=list
    )


class RecommendedAction(BaseModel):
    priority: Literal[
        "low",
        "medium",
        "high",
        "critical"
    ]

    action: str

    target_type: str

    target_name: str

    reason: str


class DeepAIAnalysis(BaseModel):
    executive_summary: AnalysisSection

    production_analysis: AnalysisSection

    quality_analysis: AnalysisSection

    component_analysis: AnalysisSection

    supplier_analysis: AnalysisSection

    batch_analysis: AnalysisSection

    downtime_analysis: AnalysisSection

    risk_areas: list[RiskArea] = Field(
        default_factory=list
    )

    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list
    )


# ============================================================
# GEMINI CLIENT
# ============================================================

client = None


def get_client() -> Any:
    global client

    if client is None:
        api_key = settings.GEMINI_API_KEY

        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY chưa được cấu hình. "
                "Vui lòng thêm biến môi trường hoặc file .env."
            )

        client = genai.Client(
            api_key=api_key,
        )

    return client


# ============================================================
# DATA PREPARATION
# ============================================================

def _clean_for_json(value: Any) -> Any:
    """
    Chuyển các kiểu dữ liệu pandas/numpy
    về kiểu Python thông thường để gửi cho Gemini.
    """

    if value is None:
        return None

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    if isinstance(value, dict):
        return {
            str(k): _clean_for_json(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            _clean_for_json(v)
            for v in value
        ]

    return value


def _build_ai_context(report: dict) -> dict:
    """
    Chỉ lấy những dữ liệu Python đã tính.
    Gemini không được phép tự tính lại KPI.
    """

    return {
        "kpis": _clean_for_json(
            report.get("kpis", [])
        ),

        "data_quality": _clean_for_json(
            report.get("data_quality", {})
        ),

        "daily_production": _clean_for_json(
            report.get("daily_production", [])
        ),

        "product_performance": _clean_for_json(
            report.get("product_performance", [])
        ),

        "model_performance": _clean_for_json(
            report.get("model_performance", [])
        ),

        "component_performance": _clean_for_json(
            report.get("component_performance", [])
        ),

        "supplier_performance": _clean_for_json(
            report.get("supplier_performance", [])
        ),

        "batch_performance": _clean_for_json(
            report.get("batch_performance", [])
        ),

        "defect_type_analysis": _clean_for_json(
            report.get("defect_type_analysis", [])
        ),

        "data_scope": _clean_for_json(
            report.get("data_scope", {})
        ),
    }


# ============================================================
# PROMPT
# ============================================================

def _build_prompt(context: dict) -> str:

    return f"""
Bạn là AI chuyên phân tích dữ liệu sản xuất phần cứng.

Nhiệm vụ của bạn là phân tích sâu dataset được cung cấp.

QUY TẮC BẮT BUỘC:

1. Chỉ sử dụng thông tin có trong DATA CONTEXT.

2. Không được tự tạo số liệu.

3. Không được thay đổi bất kỳ số liệu nào đã được Python tính toán.

4. Không được suy đoán nguyên nhân lỗi nếu dataset không cung cấp
   dữ liệu nguyên nhân.

5. Phân biệt rõ:
   - Observation: điều thực sự quan sát được từ dữ liệu.
   - Investigation point: điều cần kiểm tra thêm.
   - Root cause: nguyên nhân thực tế.

6. KHÔNG ĐƯỢC tuyên bố root cause nếu dataset không có root cause.

7. Nếu cần đưa ra hướng điều tra, phải sử dụng cách diễn đạt:
   "cần kiểm tra", "nên rà soát", "cần đối chiếu",
   "có thể điều tra thêm".

8. Risk area phải được chọn từ các entity thực sự tồn tại
   trong dữ liệu.

9. evidence_keys CHỈ được sử dụng các giá trị:
   production
   defects
   defect_rate
   downtime

10. Không được đưa số liệu trực tiếp vào evidence_keys.

11. Không được tạo entity không tồn tại trong dataset.

12. Nếu dữ liệu không đủ để kết luận một phần phân tích,
    hãy nói rõ rằng dữ liệu hiện tại chưa đủ.

13. Ưu tiên phát hiện:
    - tỷ lệ lỗi cao
    - số lỗi cao
    - downtime cao
    - sản lượng bất thường
    - dữ liệu chất lượng thấp
    - batch bất thường
    - supplier/model/component có dấu hiệu cần kiểm tra

14. Recommended actions phải xuất phát từ observation,
    không được giả định nguyên nhân.

DATA CONTEXT:

{context}
"""


# ============================================================
# VALIDATION
# ============================================================

def _get_entities(report: dict, entity_type: str) -> set[str]:

    mapping = {
        "product": "product_performance",
        "model": "model_performance",
        "component": "component_performance",
        "supplier": "supplier_performance",
        "batch": "batch_performance",
        "defect_type": "defect_type_analysis",
        "date": "daily_production",
    }

    field = mapping.get(entity_type)

    if not field:
        return set()

    rows = report.get(field, [])

    result = set()

    for row in rows:
        if not isinstance(row, dict):
            continue

        possible_names = [
            row.get("name"),
            row.get("product"),
            row.get("model"),
            row.get("component"),
            row.get("supplier"),
            row.get("batch"),
            row.get("defect_type"),
            row.get("date"),
        ]

        for name in possible_names:
            if name is not None:
                result.add(str(name))

    return result


def _validate_risk_areas(
    analysis: DeepAIAnalysis,
    report: dict,
) -> DeepAIAnalysis:

    valid_risks: list[RiskArea] = []

    for risk in analysis.risk_areas:

        valid_entities = _get_entities(
            report,
            risk.entity_type,
        )

        if risk.entity_name not in valid_entities:
            continue

        valid_risks.append(risk)

    analysis.risk_areas = valid_risks

    return analysis


# ============================================================
# MAIN AI FUNCTION
# ============================================================

def analyze_hardware_deep(
    report: dict,
) -> dict:

    context = _build_ai_context(report)

    prompt = _build_prompt(context)

    response = get_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": DeepAIAnalysis,
            "temperature": 0.2,
        },
    )

    analysis = DeepAIAnalysis.model_validate_json(
        response.text
    )

    analysis = _validate_risk_areas(
        analysis,
        report,
    )

    analysis = analysis.model_dump()

    analysis = enrich_risk_areas(
        analysis,
        report,
    )

    return analysis


def _find_entity_row(
    report: dict,
    entity_type: str,
    entity_name: str,
) -> dict | None:

    mapping = {
        "product": "product_performance",
        "model": "model_performance",
        "component": "component_performance",
        "supplier": "supplier_performance",
        "batch": "batch_performance",
        "defect_type": "defect_type_analysis",
        "date": "daily_production",
    }

    field = mapping.get(entity_type)

    if not field:
        return None

    rows = report.get(field, [])

    for row in rows:

        if not isinstance(row, dict):
            continue

        names = [
            row.get("name"),
            row.get("product"),
            row.get("model"),
            row.get("component"),
            row.get("supplier"),
            row.get("batch"),
            row.get("defect_type"),
            row.get("date"),
        ]

        if entity_name in [
            str(x) for x in names
            if x is not None
        ]:
            return row

    return None


def enrich_risk_areas(
    analysis: dict,
    report: dict,
) -> dict:

    for risk in analysis.get("risk_areas", []):

        row = _find_entity_row(
            report,
            risk["entity_type"],
            risk["entity_name"],
        )

        evidence = {}

        if row:

            if "production" in risk["evidence_keys"]:
                evidence["production"] = row.get(
                    "production"
                )

            if "defects" in risk["evidence_keys"]:
                evidence["defects"] = row.get(
                    "defects"
                )

            if "defect_rate" in risk["evidence_keys"]:
                evidence["defect_rate"] = row.get(
                    "defect_rate"
                )

            if "downtime" in risk["evidence_keys"]:
                evidence["downtime"] = row.get(
                    "downtime"
                )

        risk["evidence"] = evidence

    return analysis