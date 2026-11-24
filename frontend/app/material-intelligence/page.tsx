"use client";

import { useState } from "react";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";

type MaterialRow = {
  material: string;
  material_types: string[];
  unit: string | null;
  currency: string | null;
  planned_consumption: number | null;
  actual_consumption: number | null;
  consumption_variance: number | null;
  consumption_variance_percent: number | null;
  material_usage_rate: number | null;
  material_loss: number | null;
  scrap_material: number | null;
  material_yield_percent: number | null;
  waste_rate_percent: number | null;
  material_cost: number | null;
  material_defect_rate_percent: number | null;
  material_lot_count: number;
};

type MaterialResult = {
  executive_summary: string;
  ai_status: string;
  ai_error: string | null;
  data_quality: {
    quality_score: number;
    rows_used_for_analysis: number;
    warnings: string[];
  };
  data_structure: {
    material_count: number;
    material_lot_count: number;
    supplier_count: number;
    product_count: number;
    process_count: number;
    production_batch_count: number;
  };
  kpi_results: Record<
    string,
    {
      value: number | null;
      unit: string;
      basis: string;
      record_count: number;
    }
  >;
  material_performance: MaterialRow[];
  consumption_by_unit: Array<{
    unit: string;
    planned_consumption: number;
    actual_consumption: number;
  }>;
  process_material_loss: Array<{
    process: string;
    material: string;
    unit: string;
    actual_consumption: number;
    material_loss: number;
    waste_rate_percent: number | null;
  }>;
  material_traceability: Array<{
    material_lot: string;
    material: string | null;
    supplier: string | null;
    product: string | null;
    process: string | null;
    production_batch: string | null;
    actual_consumption: number | null;
    defects: number | null;
    defect_rate_percent: number | null;
    source_rows: number[];
  }>;
  material_product_mapping: Array<{
    material: string;
    supplier: string | null;
    product: string;
    process: string | null;
    production_batch: string | null;
    material_unit: string | null;
    material_lots: string[];
    actual_consumption: number | null;
    defects: number | null;
    defect_rate_percent: number | null;
  }>;
  defect_associated_lots: Array<{
    material_lot: string;
    material: string | null;
    supplier: string | null;
    product: string | null;
    process: string | null;
    defects: number | null;
    defect_rate_percent: number | null;
  }>;
  supplier_quality: Array<{
    supplier: string;
    defects: number | null;
    defect_rate_percent: number | null;
    monthly_volatility_percentage_points: number | null;
    risk_level: string;
    materials: string[];
  }>;
  supplier_quality_trend: Array<{
    supplier: string;
    period: string;
    defect_rate_percent: number;
    record_count: number;
  }>;
  management_answers: Record<
    string,
    { answer: string; evidence_ids: string[] }
  >;
  ai_interpretation: {
    executive_summary: { text: string; evidence_ids: string[] };
    answers: Array<{ topic: string; text: string; evidence_ids: string[] }>;
    root_cause_hypotheses: Array<{ text: string; evidence_ids: string[] }>;
    recommendations: Array<{ text: string; evidence_ids: string[] }>;
    limitations: string[];
  } | null;
  evidence_references: Array<{
    id: string;
    metric: string;
    value: unknown;
    unit: string;
  }>;
  analysis_limitations: string[];
};

const ANSWER_LABELS: Record<string, string> = {
  highest_material_loss: "Nguyên liệu có hao hụt cao",
  defective_lots: "Lô có liên hệ với lỗi",
  unstable_suppliers: "Nhà cung cấp cần xem xét",
  consumption_over_plan: "Tiêu hao vượt định mức",
  production_risk: "Rủi ro vật liệu với sản xuất",
};

const KPI_LABELS: Record<string, string> = {
  planned_consumption: "Planned consumption",
  actual_consumption: "Actual consumption",
  consumption_variance: "Consumption variance",
  consumption_variance_percent: "Variance percent",
  material_usage_rate: "Material usage rate",
  material_yield: "Material yield",
  material_loss: "Material loss",
  scrap_material: "Scrap material",
  waste_rate: "Waste rate",
  material_cost: "Actual material cost",
  planned_material_cost: "Planned material cost",
  supplier_defect_rate: "Supplier defect rate",
};

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value == null
    ? "Chưa đủ dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function MaterialTable({ rows }: { rows: MaterialRow[] }) {
  return (
    <div className="production-table-scroll">
      <table className="production-table">
        <thead>
          <tr>
            <th>Material</th>
            <th>Type</th>
            <th>Planned</th>
            <th>Actual</th>
            <th>Variance</th>
            <th>Usage / product</th>
            <th>Loss / waste</th>
            <th>Yield</th>
            <th>Defect rate</th>
            <th>Cost</th>
            <th>Lots</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.material}-${row.unit}-${row.currency}`}>
              <th scope="row">{row.material}</th>
              <td>{row.material_types.join(", ") || "Chưa phân loại"}</td>
              <td>
                {formatNumber(row.planned_consumption)} {row.unit ?? ""}
              </td>
              <td>
                {formatNumber(row.actual_consumption)} {row.unit ?? ""}
              </td>
              <td>
                {formatNumber(row.consumption_variance)} {row.unit ?? ""}
                {row.consumption_variance_percent == null
                  ? ""
                  : ` (${formatNumber(row.consumption_variance_percent)}%)`}
              </td>
              <td>{formatNumber(row.material_usage_rate)}</td>
              <td>
                {formatNumber(row.material_loss)} {row.unit ?? ""}
                {row.waste_rate_percent == null
                  ? ""
                  : ` (${formatNumber(row.waste_rate_percent)}%)`}
              </td>
              <td>
                {row.material_yield_percent == null
                  ? "Chưa đủ dữ liệu"
                  : `${formatNumber(row.material_yield_percent)}%`}
              </td>
              <td>
                {row.material_defect_rate_percent == null
                  ? "Chưa đủ dữ liệu"
                  : `${formatNumber(row.material_defect_rate_percent)}%`}
              </td>
              <td>
                {formatNumber(row.material_cost)}{" "}
                {row.currency ?? "tiền tệ chưa ghi"}
              </td>
              <td>{row.material_lot_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MaterialIntelligencePage() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [productionUnit, setProductionUnit] = useState("unit");
  const [includeAi, setIncludeAi] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MaterialResult | null>(null);

  async function submitAnalysis(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("source_timezone", sourceTimezone);
    formData.append("production_unit", productionUnit);
    formData.append("include_ai", String(includeAi));
    try {
      const response = await apiFetch("/api/material-intelligence/analyze", {
        method: "POST",
        body: formData,
      });
      const body: unknown = await response.json();
      if (!response.ok) {
        const detail =
          typeof body === "object" &&
          body !== null &&
          "detail" in body &&
          typeof body.detail === "string"
            ? body.detail
            : `Phân tích thất bại (HTTP ${response.status}).`;
        throw new Error(detail);
      }
      setResult(body as MaterialResult);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể phân tích dữ liệu vật liệu.",
      );
    } finally {
      setLoading(false);
    }
  }

  const aiAnswers = result?.ai_interpretation?.answers;

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.5</span>
            <h1>Material Intelligence</h1>
            <p>
              Theo dõi tiêu hao, hao hụt, lô nguyên liệu, chất lượng nhà cung
              cấp và liên hệ với lỗi sản xuất.
            </p>
          </div>
        </header>

        <form className="executive-card production-upload" onSubmit={submitAnalysis}>
          <FileDropzone
            accept=".csv,.xlsx,.xlsm,.xls"
            title="File vật liệu / sản xuất"
            files={file ? [file] : []}
            onFilesSelected={(selected) => {
              setFile(selected[0] ?? null);
              setResult(null);
              setError("");
            }}
          />
          <label>
            Source timezone (IANA)
            <input
              value={sourceTimezone}
              onChange={(event) => setSourceTimezone(event.target.value)}
              placeholder="UTC hoặc Asia/Ho_Chi_Minh"
            />
          </label>
          <label>
            Production unit
            <input
              value={productionUnit}
              onChange={(event) => setProductionUnit(event.target.value)}
              maxLength={40}
            />
          </label>
          <label className="production-ai-toggle">
            <input
              type="checkbox"
              checked={includeAi}
              onChange={(event) => setIncludeAi(event.target.checked)}
            />
            Cho phép gửi phần tổng hợp cho Gemini diễn giải (tùy chọn)
          </label>
          <button type="submit" disabled={loading || !file}>
            {loading ? "Đang phân tích…" : "Phân tích vật liệu"}
          </button>
          {error && (
            <p className="executive-error" role="alert">
              {error}
            </p>
          )}
        </form>

        {loading && (
          <p className="executive-loading" role="status">
            Đang chuẩn hóa vật liệu, đơn vị, lot và phân tích tiêu hao…
          </p>
        )}

        {result && (
          <>
            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">MATERIAL OVERVIEW</span>
                  <h2>{result.executive_summary}</h2>
                </div>
                <span>AI: {result.ai_status}</span>
              </div>
              {result.ai_error && (
                <p className="executive-data-context">{result.ai_error}</p>
              )}
              <p className="executive-data-context">
                {result.data_structure.material_lot_count} lots ·{" "}
                {result.data_structure.supplier_count} suppliers ·{" "}
                {result.data_structure.product_count} products ·{" "}
                {result.data_structure.process_count} processes · data quality{" "}
                {formatNumber(result.data_quality.quality_score)}%.
              </p>
            </section>

            <section className="executive-kpi-grid" aria-label="Material KPIs">
              {Object.entries(result.kpi_results).map(([key, metric]) => (
                <article className="executive-kpi-card" key={key}>
                  <span>{KPI_LABELS[key] ?? key}</span>
                  <strong>
                    {metric.value == null
                      ? "Chưa đủ dữ liệu"
                      : `${formatNumber(metric.value)} ${metric.unit}`}
                  </strong>
                  <small>{metric.basis.replaceAll("_", " ")}</small>
                </article>
              ))}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">MATERIAL PERFORMANCE</span>
                  <h2>Tiêu hao, yield, scrap và chi phí</h2>
                </div>
              </div>
              {result.material_performance.length === 0 ? (
                <p className="executive-empty">
                  File chưa có Material để nhóm dữ liệu.
                </p>
              ) : (
                <MaterialTable rows={result.material_performance} />
              )}
            </section>

            <div className="executive-two-column">
              <section className="executive-card">
                <h2>Hao hụt theo công đoạn</h2>
                {result.process_material_loss.length === 0 ? (
                  <p className="executive-empty">
                    Cần Process, Actual Consumption và Material Loss hoặc Scrap.
                  </p>
                ) : (
                  <ul>
                    {result.process_material_loss.map((item) => (
                      <li key={`${item.process}-${item.material}-${item.unit}`}>
                        <strong>{item.process}</strong> · {item.material}:{" "}
                        {formatNumber(item.material_loss)} {item.unit} waste
                        {" "}({formatNumber(item.waste_rate_percent)}%).
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section className="executive-card">
                <h2>Supplier quality & risk</h2>
                {result.supplier_quality.length === 0 ? (
                  <p className="executive-empty">
                    Cần Supplier cùng dữ liệu lỗi và inspected/production.
                  </p>
                ) : (
                  <div className="production-table-scroll">
                    <table className="production-table">
                      <thead>
                        <tr>
                          <th>Supplier</th>
                          <th>Defect rate</th>
                          <th>Volatility</th>
                          <th>Risk</th>
                          <th>Materials</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.supplier_quality.map((item) => (
                          <tr key={item.supplier}>
                            <th scope="row">{item.supplier}</th>
                            <td>
                              {item.defect_rate_percent == null
                                ? "Chưa đủ dữ liệu"
                                : `${formatNumber(item.defect_rate_percent)}%`}
                            </td>
                            <td>
                              {formatNumber(item.monthly_volatility_percentage_points)}
                            </td>
                            <td>{item.risk_level}</td>
                            <td>{item.materials.join(", ") || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>

            <section className="executive-card">
              <h2>Material-to-product / production batch mapping</h2>
              {result.material_product_mapping.length === 0 ? (
                <p className="executive-empty">
                  Cần Material và Product để tạo mapping.
                </p>
              ) : (
                <div className="production-table-scroll">
                  <table className="production-table">
                    <thead>
                      <tr>
                        <th>Material</th>
                        <th>Product</th>
                        <th>Supplier</th>
                        <th>Process</th>
                        <th>Production batch</th>
                        <th>Material lots</th>
                        <th>Actual consumption</th>
                        <th>Defect rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.material_product_mapping.map((item, index) => (
                        <tr
                          key={`${item.material}-${item.product}-${item.process}-${item.production_batch}-${index}`}
                        >
                          <th scope="row">{item.material}</th>
                          <td>{item.product}</td>
                          <td>{item.supplier ?? "—"}</td>
                          <td>{item.process ?? "—"}</td>
                          <td>{item.production_batch ?? "—"}</td>
                          <td>{item.material_lots.join(", ") || "—"}</td>
                          <td>
                            {formatNumber(item.actual_consumption)}{" "}
                            {item.material_unit ?? ""}
                          </td>
                          <td>
                            {item.defect_rate_percent == null
                              ? "Chưa đủ dữ liệu"
                              : `${formatNumber(item.defect_rate_percent)}%`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="executive-card">
              <h2>Lot / batch traceability</h2>
              {result.material_traceability.length === 0 ? (
                <p className="executive-empty">
                  Cần Material Lot để tạo liên kết lô → supplier → sản phẩm / công
                  đoạn / production batch.
                </p>
              ) : (
                <div className="production-table-scroll">
                  <table className="production-table">
                    <thead>
                      <tr>
                        <th>Material lot</th>
                        <th>Material</th>
                        <th>Supplier</th>
                        <th>Product / process</th>
                        <th>Production batch</th>
                        <th>Defects / rate</th>
                        <th>Source rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.material_traceability.map((item, index) => (
                        <tr
                          key={`${item.material_lot}-${item.material}-${item.product}-${item.process}-${index}`}
                        >
                          <th scope="row">{item.material_lot}</th>
                          <td>{item.material ?? "—"}</td>
                          <td>{item.supplier ?? "—"}</td>
                          <td>
                            {item.product ?? "—"} / {item.process ?? "—"}
                          </td>
                          <td>{item.production_batch ?? "—"}</td>
                          <td>
                            {formatNumber(item.defects, 0)} /{" "}
                            {formatNumber(item.defect_rate_percent)}%
                          </td>
                          <td>{item.source_rows.join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="executive-card">
              <h2>Lô có liên hệ với lỗi</h2>
              {result.defect_associated_lots.length === 0 ? (
                <p className="executive-empty">
                  Chưa có lot nào có defect được liên kết trong dữ liệu.
                </p>
              ) : (
                <ul>
                  {result.defect_associated_lots.map((item, index) => (
                    <li key={`${item.material_lot}-${item.product}-${index}`}>
                      <strong>{item.material_lot}</strong> · {item.material ?? "—"} ·{" "}
                      supplier {item.supplier ?? "—"} · {formatNumber(item.defects, 0)}{" "}
                      defects ({formatNumber(item.defect_rate_percent)}%). Chỉ là
                      liên hệ quan sát được, không kết luận nguyên nhân.
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">MATERIAL INTERPRETATION</span>
                  <h2>Ưu tiên điều tra</h2>
                </div>
              </div>
              <div className="production-answer-grid">
                {Object.entries(ANSWER_LABELS).map(([topic, label]) => {
                  const aiAnswer = aiAnswers?.find(
                    (answer) => answer.topic === topic,
                  );
                  const pythonAnswer = result.management_answers[topic];
                  return (
                    <article key={topic}>
                      <h3>{label}</h3>
                      <p>{aiAnswer?.text ?? pythonAnswer?.answer}</p>
                      <small>
                        Evidence:{" "}
                        {(aiAnswer?.evidence_ids ?? pythonAnswer?.evidence_ids ?? [])
                          .join(", ") || "Chưa đủ bằng chứng"}
                      </small>
                    </article>
                  );
                })}
              </div>
              {result.ai_interpretation?.recommendations.length ? (
                <div>
                  <h3>Khuyến nghị Gemini</h3>
                  <ul>
                    {result.ai_interpretation.recommendations.map((item, index) => (
                      <li key={`${item.evidence_ids.join("-")}-${index}`}>
                        {item.text} · evidence {item.evidence_ids.join(", ")}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </section>

            {result.analysis_limitations.length > 0 && (
              <section className="executive-card">
                <h2>Giới hạn dữ liệu</h2>
                <ul>
                  {result.analysis_limitations.map((limitation) => (
                    <li key={limitation}>{limitation}</li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}
