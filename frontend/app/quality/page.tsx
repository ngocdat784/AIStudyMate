"use client";

import { useMemo, useState } from "react";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";

type Metric = {
  value: number | null;
  unit: string;
  basis: string;
  record_count: number;
  evidence_id: string | null;
};

type Breakdown = {
  product?: string;
  process?: string;
  line?: string;
  shift?: string;
  inspected: number | null;
  defects: number;
  defect_rate_percent: number | null;
  record_count: number;
  evidence_id: string;
};

type ChartPoint = {
  sample: string;
  period?: string | null;
  value: number | null;
  sample_size?: number | null;
  lower_control_limit?: number | null;
  upper_control_limit?: number | null;
  out_of_control: boolean;
};

type ControlChart = {
  chart_type: string;
  scope: string;
  status: string;
  stability: string;
  center_line: number | null;
  lower_control_limit: number | null;
  upper_control_limit: number | null;
  points: ChartPoint[];
  out_of_control_points: ChartPoint[];
  reason?: string | null;
  evidence_id?: string;
};

type QualityResult = {
  schema_version: string;
  executive_summary: string;
  ai_status: string;
  ai_error: string | null;
  analysis_history_id: number;
  data_quality: {
    quality_score: number;
    quality_status: string;
    rows_read: number;
    rows_used_for_analysis: number;
    rejected_rows: number;
    warnings: string[];
  };
  data_structure: {
    product_count: number;
    process_count: number;
    line_count: number;
    shift_count: number;
    defect_category_count: number;
    measurement_count: number;
    subgroup_count: number;
  };
  kpi_results: Record<string, Metric>;
  defect_pareto: Array<{
    category: string;
    defects: number;
    share_percent: number;
    cumulative_share_percent: number;
    dominant: boolean;
    first_seen: string | null;
    evidence_id: string;
  }>;
  quality_by_product: Breakdown[];
  quality_by_process: Breakdown[];
  quality_by_line: Breakdown[];
  quality_by_shift: Breakdown[];
  quality_trends: Record<
    "daily" | "weekly" | "monthly",
    Array<{
      period: string;
      inspected: number | null;
      defects: number;
      defect_rate_percent: number | null;
      first_pass_yield_percent: number | null;
      record_count: number;
    }>
  >;
  quality_trend: {
    direction: string;
    change_percentage_points: number | null;
    evidence_id: string | null;
  };
  spc: {
    process_stability: {
      status: string;
      evaluated_chart_count: number;
      minimum_stability_subgroups: number;
      rule: string;
    };
    charts: {
      x_bar_r: Array<{
        characteristic: string;
        scope: string;
        x_bar: ControlChart;
        r: ControlChart;
      }>;
      p: ControlChart[];
      c: ControlChart[];
      u: ControlChart[];
    };
    capability: Array<{
      status: string;
      cp: number | null;
      cpk: number | null;
      sample_count: number;
      measurement_unit: string;
      normality_p_value: number | null;
      reason?: string | null;
      characteristic: string;
      scope: string;
    }>;
    anomalies: Array<{
      type: string;
      scope: string;
      sample: string;
      period: string | null;
      value: number | null;
      evidence_ids: string[];
    }>;
  };
  quality_anomalies: Array<{
    type: string;
    period?: string;
    scope?: string;
    sample?: string;
    observed_percent?: number;
    increase_percent?: number;
    severity?: string;
    evidence_ids: string[];
  }>;
  management_answers: Record<
    string,
    { answer: string; evidence_ids: string[] }
  >;
  recommendations: Array<{
    action: string;
    priority: string;
    evidence_ids: string[];
  }>;
  evidence_references: Array<{
    id: string;
    metric: string;
    value: unknown;
    unit: string;
    method: string;
  }>;
  analysis_limitations: string[];
  ai_interpretation: {
    executive_summary: { text: string; evidence_ids: string[] };
    answers: Array<{
      topic: string;
      text: string;
      evidence_ids: string[];
    }>;
    root_cause_hypotheses: Array<{
      text: string;
      evidence_ids: string[];
    }>;
    recommendations: Array<{
      text: string;
      evidence_ids: string[];
    }>;
  } | null;
};

const KPI_LABELS: Record<string, string> = {
  total_inspected: "Total inspected",
  total_defects: "Total defects",
  defect_rate: "Defect rate",
  fpy: "First Pass Yield",
  rty: "Rolled Throughput Yield",
  rework_rate: "Rework rate",
  scrap_rate: "Scrap rate",
};

const ANSWER_LABELS: Record<string, string> = {
  top_defect: "Loại lỗi xuất hiện nhiều nhất",
  highest_defect_process: "Công đoạn có defect rate cao",
  quality_trend: "Xu hướng chất lượng",
  risky_products: "Sản phẩm cần ưu tiên kiểm tra",
  statistical_control: "Trạng thái kiểm soát SPC",
};

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value == null
    ? "Chưa đủ dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function BreakdownTable({
  title,
  rows,
  dimension,
}: {
  title: string;
  rows: Breakdown[];
  dimension: "product" | "process" | "line" | "shift";
}) {
  return (
    <section className="executive-card">
      <div className="executive-card-heading">
        <div>
          <span className="executive-eyebrow">QUALITY COMPARISON</span>
          <h2>{title}</h2>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="executive-empty">File chưa có chiều dữ liệu này.</p>
      ) : (
        <div className="production-table-scroll">
          <table className="production-table">
            <thead>
              <tr>
                <th>Đối tượng</th>
                <th>Inspected</th>
                <th>Defects</th>
                <th>Defect rate</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.evidence_id}>
                  <th scope="row">{row[dimension] ?? "Unspecified"}</th>
                  <td>{formatNumber(row.inspected, 0)}</td>
                  <td>{formatNumber(row.defects, 0)}</td>
                  <td>
                    {row.defect_rate_percent == null
                      ? "Chưa đủ dữ liệu"
                      : `${formatNumber(row.defect_rate_percent)}%`}
                  </td>
                  <td>{row.evidence_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ControlChartCard({
  chart,
}: {
  chart: ControlChart;
}) {
  return (
    <article className="quality-chart-card">
      <div className="quality-chart-heading">
        <div>
          <h3>{chart.chart_type.toUpperCase()} · {chart.scope}</h3>
          <span>
            Stability: {chart.stability.replaceAll("_", " ")}
          </span>
        </div>
        {chart.evidence_id && <strong>{chart.evidence_id}</strong>}
      </div>
      {chart.status !== "available" ? (
        <p className="executive-empty">{chart.reason}</p>
      ) : (
        <>
          <div className="quality-chart-limits">
            <span>UCL {formatNumber(chart.upper_control_limit)}</span>
            <span>CL {formatNumber(chart.center_line)}</span>
            <span>LCL {formatNumber(chart.lower_control_limit)}</span>
          </div>
          <div className="quality-chart-points">
            {chart.points.map((point, index) => {
              const maxValue = Math.max(
                chart.upper_control_limit ?? 0,
                ...chart.points.map((item) => item.value ?? 0),
                0.0001,
              );
              return (
                <span
                  className={
                    point.out_of_control
                      ? "quality-chart-point quality-chart-point-alert"
                      : "quality-chart-point"
                  }
                  key={`${point.sample}-${index}`}
                  style={{
                    height: `${Math.max(3, ((point.value ?? 0) / maxValue) * 100)}%`,
                  }}
                  title={`${point.sample}: ${formatNumber(point.value)}${
                    point.out_of_control ? " · ngoài giới hạn" : ""
                  }`}
                />
              );
            })}
          </div>
          {chart.reason && (
            <p className="executive-data-context">{chart.reason}</p>
          )}
        </>
      )}
    </article>
  );
}

export default function QualityIntelligencePage() {
  const [file, setFile] = useState<File | null>(null);
  const [columnMapping, setColumnMapping] = useState("");
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [measurementUnit, setMeasurementUnit] = useState("unit");
  const [includeAi, setIncludeAi] = useState(true);
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<QualityResult | null>(null);

  const periods = useMemo(
    () => result?.quality_trends[period] ?? [],
    [period, result],
  );

  async function submitAnalysis(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Chọn file CSV hoặc XLSX trước khi phân tích.");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("source_timezone", sourceTimezone);
    formData.append("measurement_unit", measurementUnit);
    formData.append("include_ai", String(includeAi));
    if (columnMapping.trim()) {
      formData.append("column_mapping", columnMapping);
    }
    try {
      const response = await apiFetch("/api/quality/analyze", {
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
      setResult(body as QualityResult);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể phân tích dữ liệu chất lượng.",
      );
    } finally {
      setLoading(false);
    }
  }

  const report = result;
  const aiAnswers = report?.ai_interpretation?.answers;
  const charts = report
    ? [
        ...report.spc.charts.x_bar_r.flatMap((item) => [
          item.x_bar,
          item.r,
        ]),
        ...report.spc.charts.p,
        ...report.spc.charts.c,
        ...report.spc.charts.u,
      ]
    : [];

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.2</span>
            <h1>Quality Intelligence</h1>
            <p>
              Phân tích lỗi, yield và kiểm soát quy trình dựa trên dữ liệu kiểm
              tra có mẫu số, phân nhóm và evidence.
            </p>
          </div>
        </header>

        <form className="executive-card quality-upload" onSubmit={submitAnalysis}>
          <FileDropzone
            accept=".csv,.xlsx"
            title="File chất lượng"
            files={file ? [file] : []}
            onFilesSelected={(selected) => {
              setFile(selected[0] ?? null);
              setResult(null);
              setError("");
            }}
          />
          <div className="quality-form-grid">
            <label>
              Source timezone (IANA)
              <input
                type="text"
                value={sourceTimezone}
                onChange={(event) => setSourceTimezone(event.target.value)}
                placeholder="UTC hoặc Asia/Ho_Chi_Minh"
              />
            </label>
            <label>
              Measurement unit
              <input
                type="text"
                value={measurementUnit}
                onChange={(event) => setMeasurementUnit(event.target.value)}
                maxLength={40}
              />
            </label>
          </div>
          <label>
            Column mapping JSON (tùy chọn)
            <textarea
              value={columnMapping}
              onChange={(event) => setColumnMapping(event.target.value)}
              placeholder={'{"Sample Size":"Inspected Quantity","Reading":"Measurement Value"}'}
              rows={3}
            />
          </label>
          <label className="production-ai-toggle">
            <input
              type="checkbox"
              checked={includeAi}
              onChange={(event) => setIncludeAi(event.target.checked)}
            />
            Yêu cầu Gemini diễn giải (kết quả Python luôn được giữ)
          </label>
          <button type="submit" disabled={loading || !file}>
            {loading ? "Đang phân tích…" : "Phân tích chất lượng"}
          </button>
          {error && (
            <p className="executive-error" role="alert">
              {error}
            </p>
          )}
        </form>

        {loading && (
          <p className="executive-loading" role="status">
            Đang chuẩn hóa dữ liệu kiểm tra, tính KPI và SPC…
          </p>
        )}

        {report && (
          <>
            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">QUALITY OVERVIEW</span>
                  <h2>{report.executive_summary}</h2>
                </div>
                <span>AI: {report.ai_status}</span>
              </div>
              {report.ai_error && (
                <p className="executive-data-context">{report.ai_error}</p>
              )}
              <p className="executive-data-context">
                Data quality {formatNumber(report.data_quality.quality_score)}%
                {" · "}
                {report.data_quality.rows_used_for_analysis.toLocaleString(
                  "vi-VN",
                )}{" "}
                dòng hợp lệ · {report.data_structure.defect_category_count} nhóm
                lỗi · {report.data_structure.measurement_count} phép đo ·{" "}
                {report.data_structure.subgroup_count} subgroup.
              </p>
            </section>

            <section className="executive-kpi-grid" aria-label="Quality KPIs">
              {Object.entries(report.kpi_results).map(([key, metric]) => (
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
                  <span className="executive-eyebrow">DEFECT PARETO</span>
                  <h2>Nhóm lỗi và tỷ trọng tích lũy</h2>
                </div>
              </div>
              {report.defect_pareto.length === 0 ? (
                <p className="executive-empty">
                  Chưa có Defects hoặc phân loại lỗi để lập Pareto.
                </p>
              ) : (
                <div className="quality-pareto-list">
                  {report.defect_pareto.map((item) => (
                    <div className="quality-pareto-row" key={item.category}>
                      <strong>{item.category}</strong>
                      <div
                        className="quality-pareto-track"
                        aria-label={`${formatNumber(item.share_percent)} percent`}
                      >
                        <span
                          style={{
                            width: `${Math.min(100, item.share_percent)}%`,
                          }}
                        />
                      </div>
                      <span>
                        {formatNumber(item.defects, 0)} ·{" "}
                        {formatNumber(item.share_percent)}% · cumulative{" "}
                        {formatNumber(item.cumulative_share_percent)}%
                      </span>
                      <small>
                        {item.dominant ? "Pareto focus" : "Other"} · first seen{" "}
                        {item.first_seen ?? "unknown"} · {item.evidence_id}
                      </small>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">QUALITY TREND</span>
                  <h2>Defect rate theo thời gian</h2>
                </div>
                <label>
                  Kỳ
                  <select
                    value={period}
                    onChange={(event) =>
                      setPeriod(event.target.value as typeof period)
                    }
                  >
                    <option value="daily">Ngày</option>
                    <option value="weekly">Tuần</option>
                    <option value="monthly">Tháng</option>
                  </select>
                </label>
              </div>
              {periods.length === 0 ? (
                <p className="executive-empty">
                  Thiếu timestamp hợp lệ để lập xu hướng.
                </p>
              ) : (
                <div className="quality-period-list">
                  {periods.map((item) => (
                    <div className="quality-period-row" key={item.period}>
                      <strong>{item.period}</strong>
                      <span>
                        {formatNumber(item.defect_rate_percent)}% defect rate
                      </span>
                      <span>{formatNumber(item.inspected, 0)} inspected</span>
                      <span>
                        {formatNumber(item.first_pass_yield_percent)}% FPY
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <p className="executive-data-context">
                Xu hướng: {report.quality_trend.direction.replaceAll("_", " ")} ·
                thay đổi {formatNumber(report.quality_trend.change_percentage_points)}{" "}
                percentage points.
              </p>
            </section>

            <div className="executive-two-column">
              <BreakdownTable
                title="Product quality comparison"
                rows={report.quality_by_product}
                dimension="product"
              />
              <BreakdownTable
                title="Process quality comparison"
                rows={report.quality_by_process}
                dimension="process"
              />
              <BreakdownTable
                title="Line quality comparison"
                rows={report.quality_by_line}
                dimension="line"
              />
              <BreakdownTable
                title="Shift quality comparison"
                rows={report.quality_by_shift}
                dimension="shift"
              />
            </div>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">SPC DASHBOARD</span>
                  <h2>
                    Process stability:{" "}
                    {report.spc.process_stability.status.replaceAll("_", " ")}
                  </h2>
                </div>
                <span>
                  {report.spc.process_stability.evaluated_chart_count} chart(s)
                  evaluated
                </span>
              </div>
              {charts.length === 0 ? (
                <p className="executive-empty">
                  Chưa có subgroup hoặc sample đủ điều kiện cho biểu đồ SPC.
                </p>
              ) : (
                <div className="quality-chart-grid">
                  {charts.map((chart, index) => (
                    <ControlChartCard
                      chart={chart}
                      key={`${chart.chart_type}-${chart.scope}-${index}`}
                    />
                  ))}
                </div>
              )}
              <h3>Process capability</h3>
              {report.spc.capability.length === 0 ? (
                <p className="executive-empty">
                  Chưa có characteristic liên tục để tính Cp/Cpk.
                </p>
              ) : (
                <div className="production-table-scroll">
                  <table className="production-table">
                    <thead>
                      <tr>
                        <th>Characteristic / scope</th>
                        <th>Cp</th>
                        <th>Cpk</th>
                        <th>Normality p</th>
                        <th>Status / reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.spc.capability.map((item, index) => (
                        <tr key={`${item.characteristic}-${item.scope}-${index}`}>
                          <th scope="row">
                            {item.characteristic} · {item.scope}
                          </th>
                          <td>{formatNumber(item.cp)}</td>
                          <td>{formatNumber(item.cpk)}</td>
                          <td>{formatNumber(item.normality_p_value, 4)}</td>
                          <td>{item.status}: {item.reason ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="executive-data-context">
                Stability uses 3-sigma control limits and requires at least{" "}
                {report.spc.process_stability.minimum_stability_subgroups} samples
                for a stability conclusion. Cp/Cpk require stable X-bar/R,
                specification limits, a normality screen, and sufficient
                measurements.
              </p>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">AI QUALITY REPORT</span>
                  <h2>Diễn giải có evidence</h2>
                </div>
                <button type="button" onClick={() => window.print()}>
                  In / lưu báo cáo PDF
                </button>
              </div>
              <div className="production-answer-grid">
                {Object.entries(ANSWER_LABELS).map(([topic, label]) => {
                  const aiAnswer = aiAnswers?.find(
                    (answer) => answer.topic === topic,
                  );
                  const pythonAnswer = report.management_answers[topic];
                  return (
                    <article key={topic}>
                      <h3>{label}</h3>
                      <p>{aiAnswer?.text ?? pythonAnswer?.answer}</p>
                      <small>
                        Evidence:{" "}
                        {(
                          aiAnswer?.evidence_ids ??
                          pythonAnswer?.evidence_ids ??
                          []
                        ).join(", ")}
                      </small>
                    </article>
                  );
                })}
              </div>
              <h3>Ưu tiên kiểm chứng</h3>
              <ul>
                {(report.ai_interpretation?.recommendations.map((item) => ({
                  action: item.text,
                  evidence_ids: item.evidence_ids,
                })) ?? report.recommendations).map((item, index) => (
                  <li key={`${index}-${item.action}`}>
                    {item.action} (evidence {item.evidence_ids.join(", ")})
                  </li>
                ))}
              </ul>
              {report.ai_interpretation?.root_cause_hypotheses.length ? (
                <>
                  <h3>Giả thuyết cần xác minh</h3>
                  <ul>
                    {report.ai_interpretation.root_cause_hypotheses.map(
                      (item, index) => (
                        <li key={`${index}-${item.text}`}>
                          {item.text} (evidence {item.evidence_ids.join(", ")})
                        </li>
                      ),
                    )}
                  </ul>
                </>
              ) : null}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">QUALITY SIGNALS</span>
                  <h2>Bất thường và giới hạn</h2>
                </div>
              </div>
              <ul>
                {report.quality_anomalies.map((item, index) => (
                  <li key={`${item.type}-${item.period}-${item.sample}-${index}`}>
                    {item.type} · {item.period ?? item.sample ?? item.scope} ·{" "}
                    {item.severity ?? "SPC signal"} · evidence{" "}
                    {item.evidence_ids.join(", ")}
                  </li>
                ))}
                {report.analysis_limitations.map((item, index) => (
                  <li key={`limit-${index}`}>{item}</li>
                ))}
              </ul>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">EVIDENCE</span>
                  <h2>Calculation lineage</h2>
                </div>
                <span>
                  {report.evidence_references.length} items · history{" "}
                  {report.analysis_history_id}
                </span>
              </div>
              <div className="production-table-scroll">
                <table className="production-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Metric</th>
                      <th>Value</th>
                      <th>Method</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.evidence_references.map((item) => (
                      <tr key={item.id}>
                        <td>{item.id}</td>
                        <th scope="row">{item.metric}</th>
                        <td>
                          {typeof item.value === "object"
                            ? JSON.stringify(item.value)
                            : String(item.value)}{" "}
                          {item.unit}
                        </td>
                        <td>{item.method}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
