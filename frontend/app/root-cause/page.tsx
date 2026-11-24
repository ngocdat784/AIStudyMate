"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";
import { hasAuthToken, redirectToLogin } from "../../lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const AVAILABLE_DIMENSIONS = ["product", "process", "line", "shift", "machine", "material"];

type Plant = { id: number; name: string };
type EvidenceStatement = { text: string; evidence_ids: string[] };
type Candidate = {
  id: string;
  hypothesis: string;
  factor: string | null;
  factor_level: string | null;
  support_score: number;
  support_basis: string;
  sample_count: number;
  evidence_ids: string[];
  confirming_test: string;
  missing_data: string[];
  causality_status: string;
};
type CorrectiveAction = {
  candidate_id: string;
  action: string;
  priority: "high" | "medium" | "low";
  suggested_owner_role: string;
  suggested_due_days: number;
  expected_impact: string;
  verification_plan: string;
  evidence_ids: string[];
};
type FactorLevel = {
  level: string;
  inspected: number | null;
  defects: number | null;
  defect_count_basis: string;
  defect_rate_percent: number | null;
  production: number | null;
  record_count: number;
  evidence_ids: string[];
};
type FishboneField = {
  field: string;
  levels?: FactorLevel[];
  numeric_summary?: {
    count: number;
    mean: number | null;
    minimum: number | null;
    maximum: number | null;
  };
  evidence_ids?: string[];
};
type RootCauseResult = {
  schema_version: string;
  analysis_history_id: number;
  problem_statement: string;
  data_scope: {
    rows_read: number;
    rows_used: number;
    linked_observation_count: number;
    time_bucket: string;
    group_dimensions: string[];
    input_mode: string;
    source_files: string[];
    mapped_fields: string[];
    data_quality: {
      quality_score: number;
      quality_status: string;
      warnings: string[];
    };
  };
  correlation_matrix: {
    variables: string[];
    pearson: Array<Array<number | null>>;
    spearman: Array<Array<number | null>>;
    pairwise_sample_counts: number[][];
    method: string;
    omitted_variable_count: number;
  };
  notable_correlations: Array<{
    left_variable: string;
    right_variable: string;
    pearson: number;
    spearman: number;
    sample_count: number;
    evidence_ids: string[];
  }>;
  fishbone_analysis: Array<{
    category: string;
    status: string;
    missing_inputs: string[];
    factor_fields: FishboneField[];
  }>;
  five_whys: {
    status: string;
    notice: string;
    steps: Array<{
      why_number: number;
      question: string;
      answer: string | null;
      evidence_ids: string[];
    }>;
  };
  root_cause_candidates: Candidate[];
  hypothesis_score_basis: string;
  corrective_actions: CorrectiveAction[];
  baseline_metrics: Record<
    string,
    { value: number; sample_count: number }
  >;
  confidence_assessment: {
    level: string;
    basis: string;
    paired_observation_count: number;
    causality_established: boolean;
  };
  verification_plan: string;
  analysis_limitations: string[];
  evidence_references: Array<{
    id: string;
    metric: string;
    value: unknown;
    method: string;
    record_count: number;
    dimensions?: Record<string, string>;
  }>;
  ai_status: string;
  ai_error: string | null;
  ai_report: {
    problem_statement: EvidenceStatement;
    evidence_summary: EvidenceStatement[];
    fishbone_analysis: EvidenceStatement[];
    five_whys: EvidenceStatement[];
    root_cause_candidates: EvidenceStatement[];
    corrective_actions: EvidenceStatement[];
    confidence_assessment: EvidenceStatement;
    verification_plan: EvidenceStatement;
    limitations: string[];
  } | null;
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API_URL}${path}`, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Request failed (HTTP ${response.status}).`,
    );
  }
  return payload as T;
}

function formatValue(value: number | null | undefined) {
  return value == null ? "—" : value.toLocaleString(undefined, {
    maximumFractionDigits: 3,
  });
}

function dateAfterDays(days: number) {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

export default function RootCausePage() {
  const router = useRouter();
  const [plants, setPlants] = useState<Plant[]>([]);
  const [plantId, setPlantId] = useState("");
  const [problem, setProblem] = useState("");
  const [outcomeMetric, setOutcomeMetric] = useState("defect_rate");
  const [timeBucket, setTimeBucket] = useState("day");
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [downtimeUnit, setDowntimeUnit] = useState("minutes");
  const [cycleTimeUnit, setCycleTimeUnit] = useState("seconds");
  const [durationUnit, setDurationUnit] = useState("minutes");
  const [productionUnit, setProductionUnit] = useState("unit");
  const [columnMapping, setColumnMapping] = useState("");
  const [productionMapping, setProductionMapping] = useState("");
  const [qualityMapping, setQualityMapping] = useState("");
  const [dimensions, setDimensions] = useState<string[]>([
    "product",
    "process",
    "line",
    "shift",
  ]);
  const [combinedFile, setCombinedFile] = useState<File | null>(null);
  const [productionFile, setProductionFile] = useState<File | null>(null);
  const [qualityFile, setQualityFile] = useState<File | null>(null);
  const [includeAI, setIncludeAI] = useState(true);
  const [ownerId, setOwnerId] = useState("");
  const [report, setReport] = useState<RootCauseResult | null>(null);
  const [loadingPlants, setLoadingPlants] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const candidateById = useMemo(
    () => new Map(report?.root_cause_candidates.map((item) => [item.id, item]) ?? []),
    [report],
  );

  useEffect(() => {
    if (!hasAuthToken()) {
      redirectToLogin();
      return;
    }
    let active = true;
    void requestJson<Plant[]>("/api/monitoring/plants")
      .then((items) => {
        if (active) setPlants(items);
      })
      .catch((cause: unknown) => {
        if (active) {
          setError(cause instanceof Error ? cause.message : "Could not load plants.");
        }
      })
      .finally(() => {
        if (active) setLoadingPlants(false);
      });
    return () => {
      active = false;
    };
  }, []);

  function toggleDimension(dimension: string) {
    setDimensions((current) =>
      current.includes(dimension)
        ? current.filter((value) => value !== dimension)
        : [...current, dimension],
    );
  }

  async function analyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setReport(null);
    if (!combinedFile && (!productionFile || !qualityFile)) {
      setError("Upload a combined file or both production and quality files.");
      return;
    }
    if (combinedFile && (productionFile || qualityFile)) {
      setError("Use either the combined file or the two split files.");
      return;
    }
    setBusy(true);
    try {
      const data = new FormData();
      data.set("problem_statement", problem);
      data.set("time_bucket", timeBucket);
      data.set("group_dimensions", JSON.stringify(dimensions));
      data.set("outcome_metric", outcomeMetric);
      data.set("source_timezone", sourceTimezone);
      data.set("downtime_unit", downtimeUnit);
      data.set("cycle_time_unit", cycleTimeUnit);
      data.set("duration_unit", durationUnit);
      data.set("production_unit", productionUnit);
      data.set("include_ai", String(includeAI));
      if (plantId) data.set("plant_id", plantId);
      if (combinedFile) {
        data.set("file", combinedFile);
        if (columnMapping.trim()) {
          data.set("column_mapping", columnMapping);
        }
      } else {
        if (!productionFile || !qualityFile) {
          throw new Error("Both split files are required.");
        }
        data.set("production_file", productionFile);
        data.set("quality_file", qualityFile);
        if (productionMapping.trim()) {
          data.set("production_column_mapping", productionMapping);
        }
        if (qualityMapping.trim()) {
          data.set("quality_column_mapping", qualityMapping);
        }
      }
      const result = await requestJson<RootCauseResult>(
        "/api/root-cause/analyze",
        { method: "POST", body: data },
      );
      setReport(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Root-cause analysis failed.");
    } finally {
      setBusy(false);
    }
  }

  async function createImprovementCase(action: CorrectiveAction) {
    if (!report || !plantId) {
      setError("Select a plant before creating a corrective-action case.");
      return;
    }
    const candidate = candidateById.get(action.candidate_id);
    if (!candidate || Object.keys(report.baseline_metrics).length === 0) {
      setError("A ranked hypothesis and measurable baseline are required to create a case.");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const dueAt = dateAfterDays(action.suggested_due_days);
      const created = await requestJson<{ id: string }>("/api/improvements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plant_id: Number(plantId),
          problem: report.problem_statement,
          baseline: report.baseline_metrics,
          root_cause: candidate.hypothesis,
          action: action.action,
          action_priority: action.priority,
          action_due_at: dueAt,
          expected_impact: action.expected_impact,
          verification_plan: action.verification_plan,
          ...(ownerId ? { owner_user_id: Number(ownerId) } : {}),
        }),
      });
      setMessage(`Corrective-action case ${created.id} created. Opening the improvement tracker.`);
      router.push("/improvements");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create improvement case.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="integration-page root-cause-page">
      <header className="integration-hero">
        <div>
          <span className="integration-eyebrow">PHASE 2.3 · ROOT CAUSE INTELLIGENCE</span>
          <h1>Điều tra nguyên nhân có căn cứ</h1>
          <p>
            Liên kết dữ liệu sản xuất và chất lượng, xem tương quan và bằng chứng
            theo 6M. Kết quả chỉ xếp hạng giả thuyết; không khẳng định quan hệ nhân quả.
          </p>
        </div>
        <span className="integration-hero-status">
          Correlation ≠ causation
        </span>
      </header>

      {error && <div className="integration-notice error" role="alert">{error}</div>}
      {message && <div className="integration-notice success" role="status">{message}</div>}

      <section className="integration-panel">
        <div className="integration-panel-heading">
          <div>
            <span className="integration-eyebrow">ANALYSIS INPUT</span>
            <h2>Problem statement và dữ liệu liên kết</h2>
            <p>Dùng một file đã gộp hoặc hai file có chung thời gian và dimensions.</p>
          </div>
        </div>
        <form className="root-cause-form" onSubmit={analyze}>
          <label>
            Mô tả vấn đề
            <textarea
              required
              maxLength={4000}
              value={problem}
              onChange={(event) => setProblem(event.target.value)}
              placeholder="Ví dụ: tỷ lệ lỗi tăng tại công đoạn đóng gói"
            />
          </label>
          <div className="root-cause-upload-grid">
            <FileDropzone
              className="root-cause-file-drop"
              accept=".csv,.xlsx,.xlsm"
              title="File kết hợp"
              files={combinedFile ? [combinedFile] : []}
              onFilesSelected={(selected) => {
                setCombinedFile(selected[0] ?? null);
                setProductionFile(null);
                setQualityFile(null);
              }}
            />
            <span className="root-cause-or">hoặc</span>
            <FileDropzone
              className="root-cause-file-drop"
              accept=".csv,.xlsx,.xlsm"
              title="File sản xuất"
              files={productionFile ? [productionFile] : []}
              onFilesSelected={(selected) => {
                setProductionFile(selected[0] ?? null);
                setCombinedFile(null);
              }}
            />
            <FileDropzone
              className="root-cause-file-drop"
              accept=".csv,.xlsx,.xlsm"
              title="File chất lượng"
              files={qualityFile ? [qualityFile] : []}
              onFilesSelected={(selected) => {
                setQualityFile(selected[0] ?? null);
                setCombinedFile(null);
              }}
            />
          </div>
          <div className="root-cause-options">
            <label>
              Phạm vi nhà máy (để tạo case cải tiến)
              <select
                value={plantId}
                onChange={(event) => setPlantId(event.target.value)}
                disabled={loadingPlants}
              >
                <option value="">Không gắn nhà máy</option>
                {plants.map((plant) => (
                  <option key={plant.id} value={plant.id}>{plant.name}</option>
                ))}
              </select>
            </label>
            <label>
              KPI cần điều tra
              <select
                value={outcomeMetric}
                onChange={(event) => setOutcomeMetric(event.target.value)}
              >
                <option value="defect_rate">Defect rate</option>
                <option value="actual_production">Actual production</option>
                <option value="production_achievement_rate">Production achievement</option>
                <option value="downtime_minutes">Downtime</option>
              </select>
            </label>
            <label>
              Khoảng thời gian
              <select
                value={timeBucket}
                onChange={(event) => setTimeBucket(event.target.value)}
              >
                <option value="day">Ngày</option>
                <option value="week">Tuần</option>
                <option value="month">Tháng</option>
              </select>
            </label>
            <label>
              Người phụ trách (User ID, tùy chọn)
              <input
                min="1"
                type="number"
                value={ownerId}
                onChange={(event) => setOwnerId(event.target.value)}
              />
            </label>
            <label>
              Múi giờ nguồn
              <input
                required
                maxLength={80}
                value={sourceTimezone}
                onChange={(event) => setSourceTimezone(event.target.value)}
                placeholder="Asia/Ho_Chi_Minh"
              />
            </label>
            <label>
              Đơn vị downtime
              <select value={downtimeUnit} onChange={(event) => setDowntimeUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Đơn vị cycle time
              <select value={cycleTimeUnit} onChange={(event) => setCycleTimeUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Đơn vị thời lượng
              <select value={durationUnit} onChange={(event) => setDurationUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Đơn vị sản lượng
              <input
                required
                maxLength={40}
                value={productionUnit}
                onChange={(event) => setProductionUnit(event.target.value)}
              />
            </label>
          </div>
          <fieldset className="root-cause-dimensions">
            <legend>Dimensions ghép dữ liệu</legend>
            {AVAILABLE_DIMENSIONS.map((dimension) => (
              <label key={dimension}>
                <input
                  type="checkbox"
                  checked={dimensions.includes(dimension)}
                  onChange={() => toggleDimension(dimension)}
                />
                {dimension}
              </label>
            ))}
          </fieldset>
          <details className="root-cause-mapping">
            <summary>Ánh xạ cột nâng cao (JSON, tùy chọn)</summary>
            <p>
              Dùng tên canonical như Production, Inspected Quantity, Defective Units,
              Process, Operator hoặc Environment.
            </p>
            {!productionFile && !qualityFile ? (
              <label>
                Ánh xạ file kết hợp
                <textarea
                  value={columnMapping}
                  onChange={(event) => setColumnMapping(event.target.value)}
                  placeholder={'{"Actual Output":"Production","Reject Qty":"Defective Units"}'}
                />
              </label>
            ) : (
              <div className="root-cause-mapping-grid">
                <label>
                  Ánh xạ file sản xuất
                  <textarea
                    value={productionMapping}
                    onChange={(event) => setProductionMapping(event.target.value)}
                    placeholder={'{"Output":"Production","Equipment":"Machine"}'}
                  />
                </label>
                <label>
                  Ánh xạ file chất lượng
                  <textarea
                    value={qualityMapping}
                    onChange={(event) => setQualityMapping(event.target.value)}
                    placeholder={'{"Sample Size":"Inspected Quantity","NG":"Defective Units"}'}
                  />
                </label>
              </div>
            )}
          </details>
          <label className="root-cause-ai-toggle">
            <input
              type="checkbox"
              checked={includeAI}
              onChange={(event) => setIncludeAI(event.target.checked)}
            />
            Tạo diễn giải Gemini có dẫn chứng (nếu đã cấu hình)
          </label>
          <div className="integration-actions">
            <button className="integration-button primary" type="submit" disabled={busy}>
              {busy ? "Đang phân tích…" : "Phân tích nguyên nhân"}
            </button>
          </div>
        </form>
      </section>

      {report && (
        <>
          <section className="root-cause-summary-grid">
            <article className="root-cause-summary-card">
              <span>Liên kết dữ liệu</span>
              <strong>{report.data_scope.linked_observation_count}</strong>
              <small>observation · {report.data_scope.input_mode}</small>
            </article>
            <article className="root-cause-summary-card">
              <span>Data quality</span>
              <strong>{report.data_scope.data_quality.quality_score}%</strong>
              <small>{report.data_scope.rows_used} dòng dùng trong phân tích</small>
            </article>
            <article className="root-cause-summary-card">
              <span>Evidence confidence</span>
              <strong>{report.confidence_assessment.level}</strong>
              <small>{report.confidence_assessment.paired_observation_count} cặp quan sát</small>
            </article>
            <article className="root-cause-summary-card">
              <span>AI report</span>
              <strong>{report.ai_status}</strong>
              <small>{report.ai_error ?? "Kết quả Python được giữ làm nguồn sự thật."}</small>
            </article>
          </section>

          <section className="integration-panel">
            <div className="integration-panel-heading">
              <div>
                <span className="integration-eyebrow">CORRELATION INTELLIGENCE</span>
                <h2>Ma trận Pearson / Spearman</h2>
                <p>{report.correlation_matrix.method}; dữ liệu được ghép theo {report.data_scope.time_bucket} và dimensions đã chọn.</p>
              </div>
            </div>
            {report.correlation_matrix.omitted_variable_count > 0 && (
              <p className="root-cause-empty">
                Ma trận giới hạn ở {report.correlation_matrix.variables.length} biến;
                đã bỏ {report.correlation_matrix.omitted_variable_count} biến bổ sung.
              </p>
            )}
            {report.correlation_matrix.variables.length ? (
              <div className="root-cause-matrix-wrap">
                <table className="root-cause-matrix">
                  <thead>
                    <tr>
                      <th>Variable</th>
                      {report.correlation_matrix.variables.map((name) => (
                        <th key={name}>{name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {report.correlation_matrix.variables.map((row, rowIndex) => (
                      <tr key={row}>
                        <th>{row}</th>
                        {report.correlation_matrix.variables.map((column, columnIndex) => (
                          <td key={column} title={`n=${report.correlation_matrix.pairwise_sample_counts[rowIndex][columnIndex]}`}>
                            {formatValue(report.correlation_matrix.pearson[rowIndex][columnIndex])}
                            <small>S {formatValue(report.correlation_matrix.spearman[rowIndex][columnIndex])}</small>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="root-cause-empty">Chưa có đủ biến số liên kết để lập ma trận tương quan.</p>
            )}
            {report.notable_correlations.length > 0 && (
              <div className="root-cause-correlation-list">
                <h3>Mối liên hệ đáng chú ý — không suy ra nhân quả</h3>
                {report.notable_correlations.slice(0, 10).map((item) => (
                  <article key={`${item.left_variable}:${item.right_variable}`}>
                    <strong>{item.left_variable} ↔ {item.right_variable}</strong>
                    <span>Pearson {formatValue(item.pearson)} · Spearman {formatValue(item.spearman)} · n={item.sample_count}</span>
                    <small>Evidence: {item.evidence_ids.join(", ")}</small>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="integration-panel">
            <div className="integration-panel-heading">
              <div>
                <span className="integration-eyebrow">6M FISHBONE</span>
                <h2>Điều tra theo nhóm nguyên nhân</h2>
                <p>Nhóm thiếu dữ liệu được chỉ rõ; các yếu tố quan sát được liên kết evidence.</p>
              </div>
            </div>
            <div className="root-cause-fishbone-grid">
              {report.fishbone_analysis.map((category) => (
                <article className="root-cause-fishbone-card" key={category.category}>
                  <h3>{category.category}</h3>
                  {category.factor_fields.length === 0 ? (
                    <p className="root-cause-empty">Chưa có dữ liệu yếu tố.</p>
                  ) : category.factor_fields.map((factor) => (
                    <div className="root-cause-factor" key={factor.field}>
                      <strong>{factor.field}</strong>
                      {factor.numeric_summary ? (
                        <p>
                          n={factor.numeric_summary.count} · mean {formatValue(factor.numeric_summary.mean)} · range {formatValue(factor.numeric_summary.minimum)}–{formatValue(factor.numeric_summary.maximum)}
                        </p>
                      ) : (
                        <ul>
                          {(factor.levels ?? []).map((level) => (
                            <li key={level.level}>
                              {level.level}: defect rate {formatValue(level.defect_rate_percent)}% · inspected {formatValue(level.inspected)} · evidence {level.evidence_ids.join(", ")}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                  {category.missing_inputs.length > 0 && (
                    <small>Thiếu: {category.missing_inputs.join(", ")}</small>
                  )}
                </article>
              ))}
            </div>
          </section>

          <section className="root-cause-results-grid">
            <article className="integration-panel">
              <div className="integration-panel-heading">
                <div>
                  <span className="integration-eyebrow">HYPOTHESIS RANKING</span>
                  <h2>Giả thuyết cần xác minh</h2>
                  <p>{report.hypothesis_score_basis}</p>
                </div>
              </div>
              <div className="root-cause-hypothesis-list">
                {report.root_cause_candidates.map((candidate) => (
                  <article key={candidate.id}>
                    <div className="root-cause-hypothesis-heading">
                      <strong>{candidate.hypothesis}</strong>
                      <span>{candidate.support_score}/100</span>
                    </div>
                    <small>{candidate.support_basis} · n={candidate.sample_count} · {candidate.causality_status}</small>
                    <p>Kiểm chứng: {candidate.confirming_test}</p>
                    {candidate.missing_data.length > 0 && (
                      <p>Thiếu dữ liệu: {candidate.missing_data.join(", ")}</p>
                    )}
                    <small>Evidence: {candidate.evidence_ids.join(", ") || "Chưa đủ evidence"}</small>
                  </article>
                ))}
              </div>
            </article>

            <article className="integration-panel">
              <div className="integration-panel-heading">
                <div>
                  <span className="integration-eyebrow">5 WHYS</span>
                  <h2>Chuỗi điều tra</h2>
                  <p>{report.five_whys.notice}</p>
                </div>
              </div>
              <ol className="root-cause-why-list">
                {report.five_whys.steps.map((step) => (
                  <li key={step.why_number}>
                    <strong>{step.question}</strong>
                    <p>{step.answer ?? "Chờ điều tra viên ghi nhận câu trả lời có bằng chứng."}</p>
                    {step.evidence_ids.length > 0 && <small>Evidence: {step.evidence_ids.join(", ")}</small>}
                  </li>
                ))}
              </ol>
              {report.ai_report && (
                <div className="root-cause-ai-report">
                  <h3>AI Root Cause Report</h3>
                  <h4>Problem statement</h4>
                  <p>
                    {report.ai_report.problem_statement.text}{" "}
                    <small>{report.ai_report.problem_statement.evidence_ids.join(", ")}</small>
                  </p>
                  <h4>Evidence summary</h4>
                  {report.ai_report.evidence_summary.map((item, index) => (
                    <p key={`${item.evidence_ids.join("-")}-${index}`}>
                      {item.text} <small>{item.evidence_ids.join(", ")}</small>
                    </p>
                  ))}
                  <h4>Fishbone analysis</h4>
                  {report.ai_report.fishbone_analysis.map((item, index) => (
                    <p key={`fishbone-${index}`}>
                      {item.text} <small>{item.evidence_ids.join(", ")}</small>
                    </p>
                  ))}
                  <h4>Root cause candidates</h4>
                  {report.ai_report.root_cause_candidates.map((item, index) => (
                    <p key={`candidate-${index}`}>
                      {item.text} <small>{item.evidence_ids.join(", ")}</small>
                    </p>
                  ))}
                  <h4>Corrective actions</h4>
                  {report.ai_report.corrective_actions.map((item, index) => (
                    <p key={`action-${index}`}>
                      {item.text} <small>{item.evidence_ids.join(", ")}</small>
                    </p>
                  ))}
                  <h4>Confidence assessment</h4>
                  <p>
                    {report.ai_report.confidence_assessment.text}{" "}
                    <small>{report.ai_report.confidence_assessment.evidence_ids.join(", ")}</small>
                  </p>
                  <h4>Verification plan</h4>
                  <p>
                    {report.ai_report.verification_plan.text}{" "}
                    <small>{report.ai_report.verification_plan.evidence_ids.join(", ")}</small>
                  </p>
                  {report.ai_report.limitations.length > 0 && (
                    <ul>
                      {report.ai_report.limitations.map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  )}
                </div>
              )}
            </article>
          </section>

          <section className="integration-panel">
            <div className="integration-panel-heading">
              <div>
                <span className="integration-eyebrow">CORRECTIVE ACTION INTELLIGENCE</span>
                <h2>Hành động và kế hoạch xác minh</h2>
                <p>Chọn action để tạo hồ sơ theo dõi; người phụ trách mặc định là bạn nếu không nhập User ID.</p>
              </div>
            </div>
            {report.corrective_actions.length === 0 ? (
              <p className="root-cause-empty">Chưa có giả thuyết đủ bằng chứng để đề xuất hành động cụ thể.</p>
            ) : (
              <div className="root-cause-action-grid">
                {report.corrective_actions.map((action) => {
                  const candidate = candidateById.get(action.candidate_id);
                  return (
                    <article className="root-cause-action-card" key={action.candidate_id}>
                      <span className={`root-cause-priority ${action.priority}`}>{action.priority}</span>
                      <h3>{action.action}</h3>
                      <p>{candidate?.hypothesis}</p>
                      <dl>
                        <div><dt>Gợi ý phụ trách</dt><dd>{action.suggested_owner_role}</dd></div>
                        <div><dt>Hạn đề xuất</dt><dd>{action.suggested_due_days} ngày</dd></div>
                        <div><dt>Tác động</dt><dd>{action.expected_impact}</dd></div>
                        <div><dt>Xác minh</dt><dd>{action.verification_plan}</dd></div>
                      </dl>
                      <button
                        className="integration-button primary"
                        type="button"
                        disabled={busy || !plantId || Object.keys(report.baseline_metrics).length === 0}
                        onClick={() => void createImprovementCase(action)}
                      >
                        Tạo hồ sơ cải tiến
                      </button>
                    </article>
                  );
                })}
              </div>
            )}
            {!plantId && <p className="root-cause-empty">Chọn nhà máy ở phần đầu để lưu action vào Improvement tracker.</p>}
            {Object.keys(report.baseline_metrics).length === 0 && (
              <p className="root-cause-empty">Không có KPI baseline đủ mẫu; cần bổ sung dữ liệu trước khi tạo hồ sơ cải tiến.</p>
            )}
          </section>

          <section className="integration-panel">
            <div className="integration-panel-heading">
              <div>
                <span className="integration-eyebrow">REPORT & LIMITATIONS</span>
                <h2>Kết luận và giới hạn phân tích</h2>
              </div>
            </div>
            <p>{report.confidence_assessment.basis}</p>
            <p><strong>Kế hoạch xác minh:</strong> {report.verification_plan}</p>
            <ul className="root-cause-limitations">
              {report.analysis_limitations.map((item) => <li key={item}>{item}</li>)}
            </ul>
            <details>
              <summary>Xem evidence references ({report.evidence_references.length})</summary>
              <ul>
                {report.evidence_references.slice(0, 100).map((item) => (
                  <li key={item.id}>
                    <strong>{item.id} · {item.metric}</strong>: {JSON.stringify(item.value)}
                    {item.dimensions && <small> · {JSON.stringify(item.dimensions)}</small>}
                  </li>
                ))}
              </ul>
            </details>
          </section>
        </>
      )}
    </main>
  );
}
