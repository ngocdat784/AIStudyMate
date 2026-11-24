"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";
import { hasAuthToken, redirectToLogin } from "../../lib/auth";

type DomainResult = {
  key: string;
  label: string;
  status: string;
  summary: string | null;
  evidence_count: number;
  limitations: string[];
};

type CrossFinding = {
  type: string;
  domains: string[];
  variables?: string[];
  spearman_correlation?: number;
  sample_count?: number;
  material_lot?: string;
  machine?: string;
  supplier?: string;
  product?: string;
  defect_rate_percent?: number | null;
  historical_failure_day_frequency_percent?: number | null;
  forecast_production_total?: number;
  forecast_horizon_periods?: number;
  interpretation: string;
  status: string;
  evidence_ids: string[];
};

type IntegrationResult = {
  analysis_history_id: number;
  analysis_contract: {
    cross_findings_are_causal: boolean;
  };
  data_quality: {
    quality_score: number;
    quality_status: string;
    rows_used_for_analysis: number;
    warnings: string[];
  };
  domains: Record<string, DomainResult>;
  cross_intelligence: {
    status: string;
    findings: CrossFinding[];
    analysis_limitations: string[];
    minimum_correlation_sample_count: number;
    minimum_absolute_spearman_correlation: number;
  };
  ai_orchestration: {
    status: string;
    scope: string;
  };
};

const DOMAIN_ORDER = [
  "production",
  "quality",
  "root_cause",
  "machine",
  "material",
  "predictive",
];
const STATUS_LABELS: Record<string, string> = {
  available: "Đủ dữ liệu",
  limited: "Có giới hạn",
  insufficient_data: "Chưa đủ dữ liệu",
};
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

function formatNumber(value: number | null | undefined) {
  return value == null
    ? "—"
    : value.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

export default function SystemIntegrationPage() {
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState(
    "Đánh giá liên hệ giữa các yếu tố sản xuất và chất lượng.",
  );
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [includeAi, setIncludeAi] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IntegrationResult | null>(null);

  async function submitAnalysis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!hasAuthToken()) {
      redirectToLogin();
      return;
    }
    if (!file) {
      setError("Chọn file CSV hoặc XLSX trước khi phân tích.");
      return;
    }

    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("problem_statement", problem);
    formData.append("source_timezone", sourceTimezone);
    formData.append("include_ai", String(includeAi));
    try {
      const response = await apiFetch(
        `${API_URL}/api/system-integration/analyze`,
        { method: "POST", body: formData },
      );
      const body: unknown = await response.json();
      if (!response.ok) {
        const detail =
          typeof body === "object" &&
          body !== null &&
          "detail" in body &&
          typeof body.detail === "string"
            ? body.detail
            : `Tích hợp phân tích thất bại (HTTP ${response.status}).`;
        throw new Error(detail);
      }
      setResult(body as IntegrationResult);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể hoàn thành phân tích tích hợp.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">
              HARDWAREAI · PHASE 2.8
            </span>
            <h1>System Integration &amp; Acceptance</h1>
            <p>
              Chạy sáu Intelligence trên cùng một bộ dữ liệu, truy nguyên
              findings xuyên miền và lưu unified result vào Analysis History.
            </p>
          </div>
        </header>

        <form
          className="executive-card production-upload"
          onSubmit={submitAnalysis}
        >
          <FileDropzone
            accept=".csv,.xlsx"
            title="Dữ liệu hợp nhất"
            files={file ? [file] : []}
            onFilesSelected={(selected) => {
              setFile(selected[0] ?? null);
              setResult(null);
              setError("");
            }}
          />
          <label>
            Câu hỏi điều tra
            <input
              type="text"
              maxLength={4000}
              value={problem}
              onChange={(event) => setProblem(event.target.value)}
            />
          </label>
          <label>
            Source timezone (IANA)
            <input
              type="text"
              maxLength={80}
              value={sourceTimezone}
              onChange={(event) => setSourceTimezone(event.target.value)}
              placeholder="UTC hoặc Asia/Ho_Chi_Minh"
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={includeAi}
              onChange={(event) => setIncludeAi(event.target.checked)}
            />
            Bật Gemini cho diễn giải Root Cause xuyên miền
          </label>
          <button type="submit" disabled={loading}>
            {loading ? "Đang chạy sáu Intelligence…" : "Chạy nghiệm thu tích hợp"}
          </button>
        </form>

        {error && (
          <p className="executive-error" role="alert">
            {error}
          </p>
        )}

        {result && (
          <>
            <section className="executive-kpi-grid">
              <article className="executive-kpi-card">
                <span>Chất lượng dữ liệu</span>
                <strong>
                  {formatNumber(result.data_quality.quality_score)}%
                </strong>
                <small>
                  {result.data_quality.quality_status} ·{" "}
                  {result.data_quality.rows_used_for_analysis} dòng hợp lệ
                </small>
              </article>
              <article className="executive-kpi-card">
                <span>Cross-intelligence</span>
                <strong>{result.cross_intelligence.findings.length}</strong>
                <small>{result.cross_intelligence.status}</small>
              </article>
              <article className="executive-kpi-card">
                <span>AI Orchestrator</span>
                <strong>{result.ai_orchestration.status}</strong>
                <small>Root Cause cross-domain interpretation</small>
              </article>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">SIX INTELLIGENCES</span>
                  <h2>Kết quả hợp nhất</h2>
                </div>
                <Link href="/history">
                  Analysis History #{result.analysis_history_id}
                </Link>
              </div>
              <div className="executive-ranking-list">
                {DOMAIN_ORDER.map((key) => {
                  const domain = result.domains[key];
                  if (!domain) return null;
                  return (
                    <article key={domain.key}>
                      <span className="executive-ranking-index">
                        {STATUS_LABELS[domain.status] ?? domain.status}
                      </span>
                      <span className="executive-ranking-name">
                        {domain.label}
                      </span>
                      <span className="executive-ranking-metrics">
                        {domain.summary ?? "Không có executive summary"} ·{" "}
                        {domain.evidence_count} evidence
                        {domain.limitations.length > 0
                          ? ` · ${domain.limitations.length} giới hạn dữ liệu`
                          : ""}
                      </span>
                    </article>
                  );
                })}
              </div>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">
                    CROSS-INTELLIGENCE
                  </span>
                  <h2>Liên hệ xuyên miền cần xác minh</h2>
                </div>
              </div>
              <p>
                Tương quan và truy xuất lô chỉ là tín hiệu liên hệ, không chứng
                minh nguyên nhân. Ngưỡng tương quan: |Spearman| ≥{" "}
                {result.cross_intelligence.minimum_absolute_spearman_correlation},
                tối thiểu{" "}
                {result.cross_intelligence.minimum_correlation_sample_count}{" "}
                quan sát.
              </p>
              {result.cross_intelligence.findings.length === 0 ? (
                <p className="executive-empty">
                  Chưa có tín hiệu xuyên miền đủ điều kiện; không suy diễn khi
                  thiếu dữ liệu.
                </p>
              ) : (
                <ul>
                  {result.cross_intelligence.findings.map((finding, index) => (
                    <li key={`${finding.type}-${index}`}>
                      <strong>
                        {finding.domains.join(" + ")} · {finding.status}
                      </strong>
                      {finding.variables
                        ? ` · ${finding.variables.join(" ↔ ")}`
                        : ""}
                      {finding.material_lot
                        ? ` · Lô ${finding.material_lot}`
                        : ""}
                      {finding.machine
                        ? ` · Máy ${finding.machine}`
                        : ""}
                      {finding.supplier ? ` · ${finding.supplier}` : ""}
                      {finding.product ? ` · ${finding.product}` : ""}
                      {finding.spearman_correlation != null
                        ? ` · Spearman ${formatNumber(finding.spearman_correlation)} (n=${finding.sample_count})`
                        : ""}
                      {finding.defect_rate_percent != null
                        ? ` · defect rate ${formatNumber(finding.defect_rate_percent)}%`
                        : ""}
                      {finding.historical_failure_day_frequency_percent != null
                        ? ` · tần suất ngày lỗi ${formatNumber(finding.historical_failure_day_frequency_percent)}%`
                        : ""}
                      {finding.forecast_production_total != null
                        ? ` · sản lượng dự báo ${formatNumber(finding.forecast_production_total)} (${finding.forecast_horizon_periods} kỳ)`
                        : ""}
                      {` · ${finding.interpretation} · Evidence: ${finding.evidence_ids.join(", ") || "liên kết từ kết quả domain"}`}
                    </li>
                  ))}
                </ul>
              )}
              {result.cross_intelligence.analysis_limitations.length > 0 && (
                <details>
                  <summary>Giới hạn dữ liệu/phân tích</summary>
                  <ul>
                    {result.cross_intelligence.analysis_limitations.map(
                      (limitation, index) => (
                        <li key={`${index}-${limitation}`}>{limitation}</li>
                      ),
                    )}
                  </ul>
                </details>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
