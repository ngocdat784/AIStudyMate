"use client";

import { useMemo, useState } from "react";

import { apiFetch } from "../../lib/api";

type Metric = {
  value: number | null;
  unit: string;
  basis: string;
  record_count: number;
  evidence_id: string | null;
};

type Performance = {
  entity: string;
  actual_production: number;
  planned_production: number | null;
  production_achievement_rate: number | null;
  production_rate: number | null;
  throughput: number | null;
  cycle_time_seconds: number | null;
  line_efficiency_percent: number | null;
  line_utilization_percent: number | null;
  waiting_time_minutes: number | null;
  evidence_id: string;
};

type ProductionPeriod = {
  period: string;
  actual_production: number;
  actual_on_planned_rows: number | null;
  planned_production: number | null;
  production_achievement_rate: number | null;
};

type ProductionResult = {
  title: string;
  summary: string;
  ai_status: string;
  ai_error: string | null;
  production_intelligence: {
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
      timestamp_count: number;
      production_interval_count: number;
    };
    kpi_results: Record<string, Metric>;
    plan_vs_actual: {
      coverage_records: number;
      total_records: number;
    };
    production_trends: Record<
      "daily" | "weekly" | "monthly",
      ProductionPeriod[]
    >;
    line_performance: Performance[];
    shift_performance: Performance[];
    process_performance: Performance[];
    bottleneck_analysis: {
      candidates: Array<{
        process: string;
        throughput: number;
        peer_median_throughput: number;
        waiting_time_minutes: number | null;
        status: string;
        line_impact: string;
        evidence_ids: string[];
      }>;
      method: string;
    };
    production_anomalies: Array<{
      type: string;
      period: string;
      severity: string;
      evidence_ids: string[];
      actual?: number;
      planned?: number;
      shortfall_percent?: number;
      observed?: number;
      baseline_median?: number;
      observed_seconds?: number;
      upper_bound_seconds?: number;
      increase_percent?: number;
    }>;
    management_answers: Record<
      string,
      { answer: string; evidence_ids: string[] } | null
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
};

const KPI_LABELS: Record<string, string> = {
  planned_production: "Planned production",
  actual_production: "Actual production",
  production_achievement_rate: "Plan achievement",
  plan_variance: "Plan variance",
  production_rate: "Production rate",
  throughput: "Throughput",
  cycle_time: "Cycle time",
  takt_time: "Takt time",
  line_efficiency: "Line efficiency",
  line_utilization: "Line utilization",
  waiting_time: "Waiting time",
};

const ANSWER_LABELS: Record<string, string> = {
  weakest_line: "Dây chuyền cần ưu tiên",
  plan_shortfall: "Sản lượng so với kế hoạch",
  best_shift: "Ca hoạt động tốt nhất",
  bottleneck_process: "Công đoạn cần kiểm tra",
  production_decline: "Suy giảm sản xuất",
};

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value == null
    ? "Chưa đủ dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function PerformanceTable({
  title,
  rows,
}: {
  title: string;
  rows: Performance[];
}) {
  return (
    <section className="executive-card">
      <div className="executive-card-heading">
        <div>
          <span className="executive-eyebrow">SO SÁNH SẢN XUẤT</span>
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
                <th>Actual</th>
                <th>Plan achievement</th>
                <th>Throughput</th>
                <th>Cycle time</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.entity}>
                  <th scope="row">{row.entity}</th>
                  <td>{formatNumber(row.actual_production, 0)}</td>
                  <td>
                    {row.production_achievement_rate == null
                      ? "Chưa có plan"
                      : `${formatNumber(row.production_achievement_rate)}%`}
                  </td>
                  <td>{formatNumber(row.throughput)}</td>
                  <td>
                    {row.cycle_time_seconds == null
                      ? "Chưa có dữ liệu"
                      : `${formatNumber(row.cycle_time_seconds)} s`}
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

export default function ProductionIntelligencePage() {
  const [file, setFile] = useState<File | null>(null);
  const [includeAi, setIncludeAi] = useState(true);
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [cycleTimeUnit, setCycleTimeUnit] = useState("seconds");
  const [durationUnit, setDurationUnit] = useState("minutes");
  const [downtimeUnit, setDowntimeUnit] = useState("minutes");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [period, setPeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const [result, setResult] = useState<ProductionResult | null>(null);

  const periods = useMemo(
    () => result?.production_intelligence.production_trends[period] ?? [],
    [period, result],
  );
  const maximum = Math.max(
    1,
    ...periods.flatMap((item) => [
      item.actual_production,
      item.planned_production ?? 0,
    ]),
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
    formData.append("include_ai", String(includeAi));
    formData.append("source_timezone", sourceTimezone);
    formData.append("cycle_time_unit", cycleTimeUnit);
    formData.append("duration_unit", durationUnit);
    formData.append("downtime_unit", downtimeUnit);
    try {
      const response = await apiFetch("/api/production/analyze", {
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
      setResult(body as ProductionResult);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể phân tích dữ liệu sản xuất.",
      );
    } finally {
      setLoading(false);
    }
  }

  const report = result?.production_intelligence;
  const aiAnswers = report?.ai_interpretation?.answers;

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.1</span>
            <h1>Production Intelligence</h1>
            <p>
              Đối chiếu kế hoạch, sản lượng, ca, dây chuyền và nút thắt từ dữ
              liệu có bằng chứng.
            </p>
          </div>
        </header>

        <form className="executive-card production-upload" onSubmit={submitAnalysis}>
          <label>
            File sản xuất (CSV/XLSX)
            <input
              type="file"
              accept=".csv,.xlsx"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setResult(null);
                setError("");
              }}
            />
          </label>
          <label>
            Source timezone (IANA)
            <input
              type="text"
              value={sourceTimezone}
              onChange={(event) => setSourceTimezone(event.target.value)}
              placeholder="UTC hoặc Asia/Ho_Chi_Minh"
            />
          </label>
          <div className="production-unit-fields">
            <label>
              Cycle time unit
              <select
                value={cycleTimeUnit}
                onChange={(event) => setCycleTimeUnit(event.target.value)}
              >
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
            Available / operating / waiting time unit
              <select
                value={durationUnit}
                onChange={(event) => setDurationUnit(event.target.value)}
              >
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Downtime unit
              <select
                value={downtimeUnit}
                onChange={(event) => setDowntimeUnit(event.target.value)}
              >
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
          </div>
          <label className="production-ai-toggle">
            <input
              type="checkbox"
              checked={includeAi}
              onChange={(event) => setIncludeAi(event.target.checked)}
            />
            Yêu cầu Gemini diễn giải (kết quả Python luôn được giữ)
          </label>
          <button type="submit" disabled={loading || !file}>
            {loading ? "Đang phân tích…" : "Phân tích sản xuất"}
          </button>
          {error && (
            <p className="executive-error" role="alert">
              {error}
            </p>
          )}
        </form>

        {loading && (
          <p className="executive-loading" role="status">
            Đang chuẩn hóa dữ liệu và tính KPI sản xuất…
          </p>
        )}

        {result && report && (
          <>
            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">
                    PRODUCTION OVERVIEW
                  </span>
                  <h2>{result.title}</h2>
                </div>
                <span>AI: {result.ai_status}</span>
              </div>
              <p>{result.summary}</p>
              {result.ai_error && (
                <p className="executive-data-context">{result.ai_error}</p>
              )}
              <p className="executive-data-context">
                Data quality {formatNumber(report.data_quality.quality_score)}%
                {" · "}
                {report.data_quality.rows_used_for_analysis.toLocaleString(
                  "vi-VN",
                )}{" "}
                dòng hợp lệ · {report.data_structure.shift_count} ca ·{" "}
                {report.data_structure.line_count} dây chuyền ·{" "}
                {report.data_structure.process_count} công đoạn.
              </p>
            </section>

            <section className="executive-kpi-grid" aria-label="Production KPIs">
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
                  <span className="executive-eyebrow">PLAN VS ACTUAL</span>
                  <h2>Xu hướng sản lượng</h2>
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
              <div className="production-period-list">
                {periods.length === 0 ? (
                  <p className="executive-empty">
                    Thiếu timestamp hợp lệ để lập xu hướng.
                  </p>
                ) : (
                  periods.map((item) => {
                    const planActual =
                      item.planned_production == null
                        ? item.actual_production
                        : item.actual_on_planned_rows ?? 0;
                    return (
                      <div className="production-period-row" key={item.period}>
                        <strong>{item.period}</strong>
                        <div className="production-period-bars">
                          <span
                            className="production-bar production-bar-actual"
                            style={{
                              width: `${Math.max(2, (planActual / maximum) * 100)}%`,
                            }}
                            title={`Actual ${formatNumber(planActual, 0)}`}
                          />
                          {item.planned_production != null && (
                            <span
                              className="production-bar production-bar-plan"
                              style={{
                                width: `${Math.max(2, (item.planned_production / maximum) * 100)}%`,
                              }}
                              title={`Plan ${formatNumber(item.planned_production, 0)}`}
                            />
                          )}
                        </div>
                        <span>
                          {formatNumber(planActual, 0)} actual
                          {item.planned_production == null
                            ? ""
                            : ` / ${formatNumber(item.planned_production, 0)} plan`}
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
              <p className="executive-data-context">
                Actual (xanh) · Plan (vàng). So sánh chỉ dùng các dòng có kế
                hoạch tường minh.
              </p>
            </section>

            <div className="executive-two-column">
              <PerformanceTable
                title="So sánh dây chuyền"
                rows={report.line_performance}
              />
              <PerformanceTable
                title="Hiệu suất theo ca"
                rows={report.shift_performance}
              />
            </div>

            <PerformanceTable
              title="So sánh công đoạn"
              rows={report.process_performance}
            />

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">BOTTLENECK REPORT</span>
                  <h2>Ứng viên nút thắt</h2>
                </div>
              </div>
              {report.bottleneck_analysis.candidates.length === 0 ? (
                <p className="executive-empty">
                  Chưa đủ throughput theo công đoạn để xác định ứng viên.
                </p>
              ) : (
                <ul>
                  {report.bottleneck_analysis.candidates.map((item) => (
                    <li key={item.process}>
                      {item.process}: {formatNumber(item.throughput)}{" "}
                      {report.kpi_results.throughput.unit}; waiting{" "}
                      {formatNumber(item.waiting_time_minutes)} phút; evidence{" "}
                      {item.evidence_ids.join(", ")}. Chưa xác nhận ảnh hưởng
                      toàn dây chuyền.
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">AI INTERPRETATION</span>
                  <h2>Trả lời điều hành và khuyến nghị</h2>
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
                      <p>
                        {aiAnswer?.text ??
                          pythonAnswer?.answer ??
                          "Chưa đủ dữ liệu để trả lời."}
                      </p>
                      <small>
                        Evidence:{" "}
                        {(
                          aiAnswer?.evidence_ids ??
                          pythonAnswer?.evidence_ids ??
                          []
                        ).join(", ") || "chưa có"}
                      </small>
                    </article>
                  );
                })}
              </div>
              <h3>Ưu tiên cải thiện</h3>
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
                  <h3>Giả thuyết cần kiểm chứng</h3>
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
                  <span className="executive-eyebrow">ANOMALIES & LIMITS</span>
                  <h2>Tín hiệu và giới hạn dữ liệu</h2>
                </div>
              </div>
              <ul>
                {report.production_anomalies.map((item, index) => (
                  <li key={`${item.type}-${item.period}-${index}`}>
                    {item.type} · {item.period} · mức {item.severity} ·
                    evidence {item.evidence_ids.join(", ")}
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
                  <h2>Bằng chứng tính toán</h2>
                </div>
                <span>
                  {report.evidence_references.length} mục · data rows{" "}
                  {report.data_quality.rows_read}
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
