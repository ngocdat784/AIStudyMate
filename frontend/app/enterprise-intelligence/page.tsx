"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../../lib/api";
import { hasAuthToken, redirectToLogin } from "../../lib/auth";

type Domain = {
  key: string;
  label: string;
  status: "available" | "limited" | "stale" | "no_analysis";
  is_stale: boolean;
  latest_analysis: {
    id: number;
    analysis_type: string;
    title: string | null;
    summary: string | null;
    created_at: string | null;
    algorithm_version: string | null;
    ai_status: string | null;
    evidence_count: number;
    quality_score: number | null;
    limitations: string[];
  } | null;
};

type Recommendation = {
  id: string;
  rank: number;
  domain: string;
  source_analysis_history_id: number;
  action: string;
  priority: "high" | "medium" | "low";
  impact_basis: string;
  evidence_ids: string[];
  status: "suggested";
};

type Overview = {
  generated_at: string;
  period_days: number;
  enterprise_overview: {
    domain_count: number;
    analyzed_domain_count: number;
    analysis_coverage_percent: number;
    active_alert_count: number;
    alerts_by_severity_in_period: Record<string, number>;
    active_work_item_count: number;
    overdue_work_item_count: number;
    ranked_recommendation_count: number;
  };
  domains: Domain[];
  recommendations: Recommendation[];
  intelligent_alerts: Array<{
    id: number;
    severity: string;
    status: string;
    title: string;
    summary: string;
    metric: string;
    recommended_check: string;
    detected_at: string | null;
  }>;
  workflow: {
    active_cases: Array<{
      id: string;
      problem: string;
      action: string | null;
      priority: string | null;
      status: string;
      owner_user_id: number | null;
      action_due_at: string | null;
      overdue: boolean;
      escalation_status: string;
    }>;
    overdue_cases: Array<{ id: string; problem: string }>;
    escalation_policy: string;
  };
  audit_governance: {
    user_activity: Array<{
      id: number;
      actor_user_id: number | null;
      action: string;
      object_type: string;
      object_id: string | null;
      outcome: string;
      created_at: string | null;
    }>;
    ai_analysis_audit: {
      status: string;
      period_days?: number;
      by_analysis_and_status?: Array<{
        analysis_type: string;
        status: string;
        count: number;
      }>;
      sensitive_prompts_and_responses_exposed: boolean;
    };
    traceability: Array<{
      domain: string;
      analysis_history_id: number;
      algorithm_version: string | null;
      ai_status: string | null;
      source_file_sha256: string | null;
      evidence_count: number;
    }>;
  };
  reporting: {
    governed_report_count: number;
    latest_governed_reports: Array<{
      id: number;
      title: string;
      current_version: number;
      created_at: string | null;
    }>;
    report_templates: Array<{
      key: string;
      title: string;
      cadence: string;
      status: "ready" | "partial" | "no_source";
      missing_domains: string[];
      source_analysis_ids: number[];
    }>;
  };
};

const domainLinks: Record<string, string> = {
  production: "/production",
  quality: "/quality",
  machine: "/machine-intelligence",
  material: "/material-intelligence",
  predictive: "/predictive-intelligence",
  root_cause: "/root-cause",
};

const priorityLabels: Record<string, string> = {
  high: "Cao",
  medium: "Trung bình",
  low: "Thấp",
};

const domainLabels: Record<string, string> = {
  production: "Production",
  quality: "Quality",
  machine: "Machine",
  material: "Material",
  predictive: "Predictive",
  root_cause: "Root Cause",
};

function formatNumber(value: number | null | undefined, digits = 1): string {
  return value == null
    ? "—"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Chưa ghi nhận";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("vi-VN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(parsed);
}

async function loadOverview(days: number): Promise<Overview> {
  const response = await apiFetch(
    `/api/enterprise-intelligence/overview?days=${days}`,
    { cache: "no-store" },
  );
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail =
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
        ? body.detail
        : `Không thể tải Enterprise Overview (HTTP ${response.status}).`;
    throw new Error(detail);
  }
  return body as Overview;
}

export default function EnterpriseIntelligencePage() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setOverview(await loadOverview(days));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể tải dữ liệu điều hành doanh nghiệp.",
      );
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (!hasAuthToken()) {
      redirectToLogin();
      return;
    }
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.7</span>
            <h1>Enterprise Intelligence</h1>
            <p>
              Kết nối sáu Intelligence với cảnh báo, khuyến nghị, công việc
              khắc phục, audit và báo cáo có kiểm soát nguồn.
            </p>
          </div>
          <label>
            Kỳ theo dõi
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
            >
              <option value={7}>7 ngày</option>
              <option value={30}>30 ngày</option>
              <option value={90}>90 ngày</option>
              <option value={365}>365 ngày</option>
            </select>
          </label>
        </header>

        {error && <p className="executive-error" role="alert">{error}</p>}
        {loading && (
          <p className="executive-loading" role="status">
            Đang tổng hợp intelligence, cảnh báo và công việc…
          </p>
        )}

        {overview && (
          <>
            <section className="executive-kpi-grid" aria-label="Enterprise overview">
              <article className="executive-kpi-card">
                <span>Phạm vi Intelligence</span>
                <strong>
                  {overview.enterprise_overview.analyzed_domain_count}/
                  {overview.enterprise_overview.domain_count}
                </strong>
                <small>
                  {formatNumber(
                    overview.enterprise_overview.analysis_coverage_percent,
                  )}
                  % có phân tích mới trong {overview.period_days} ngày
                </small>
              </article>
              <article className="executive-kpi-card executive-kpi-alert">
                <span>Cảnh báo đang hoạt động</span>
                <strong>{overview.enterprise_overview.active_alert_count}</strong>
                <small>
                  {Object.entries(
                    overview.enterprise_overview.alerts_by_severity_in_period,
                  )
                    .map(([severity, count]) => `${severity}: ${count}`)
                    .join(" · ") || "Không có cảnh báo trong kỳ"}
                </small>
              </article>
              <article className="executive-kpi-card">
                <span>Công việc khắc phục</span>
                <strong>{overview.enterprise_overview.active_work_item_count}</strong>
                <small>
                  {overview.enterprise_overview.overdue_work_item_count} quá hạn
                  cần escalation
                </small>
              </article>
              <article className="executive-kpi-card">
                <span>Khuyến nghị ưu tiên</span>
                <strong>
                  {overview.enterprise_overview.ranked_recommendation_count}
                </strong>
                <small>Được truy nguyên về phân tích nguồn</small>
              </article>
            </section>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">SIX INTELLIGENCES</span>
                  <h2>Enterprise monitoring</h2>
                </div>
              </div>
              <div className="executive-ranking-list">
                {overview.domains.map((domain) => (
                  <article key={domain.key}>
                    <span className="executive-ranking-index">
                      {domain.status === "available"
                        ? "OK"
                        : domain.status === "limited"
                          ? "!"
                          : domain.status === "stale"
                            ? "…"
                            : "—"}
                    </span>
                    <span className="executive-ranking-name">
                      {domain.label}
                    </span>
                    <span className="executive-ranking-metrics">
                      {domain.latest_analysis
                        ? `${domain.latest_analysis.title ?? domain.latest_analysis.analysis_type} · ${formatDate(domain.latest_analysis.created_at)}`
                        : "Chưa có phân tích"}
                      {domain.latest_analysis?.summary
                        ? ` · ${domain.latest_analysis.summary}`
                        : ""}
                      {domain.latest_analysis?.limitations.length
                        ? ` · ${domain.latest_analysis.limitations.length} giới hạn dữ liệu`
                        : ""}
                    </span>
                    <Link href={domainLinks[domain.key] ?? "/history"}>
                      Mở
                    </Link>
                  </article>
                ))}
              </div>
            </section>

            <div className="executive-two-column">
              <section className="executive-card">
                <div className="executive-card-heading">
                  <div>
                    <span className="executive-eyebrow">RECOMMENDATION ENGINE</span>
                    <h2>Ưu tiên hành động</h2>
                  </div>
                  <Link href="/improvements">Theo dõi cải tiến →</Link>
                </div>
                {overview.recommendations.length === 0 ? (
                  <p className="executive-empty">
                    Chưa có khuyến nghị từ phân tích mới trong kỳ.
                  </p>
                ) : (
                  <ol className="executive-ranking-list">
                    {overview.recommendations.slice(0, 20).map((item) => (
                      <li key={item.id}>
                        <span className="executive-ranking-index">
                          #{item.rank} · {priorityLabels[item.priority]}
                        </span>
                        <span className="executive-ranking-name">
                          {item.action}
                        </span>
                        <span className="executive-ranking-metrics">
                          {domainLabels[item.domain] ?? item.domain} ·{" "}
                          {item.impact_basis} · {item.evidence_ids.length} evidence
                        </span>
                        <Link href="/history">
                          Nguồn #{item.source_analysis_history_id}
                        </Link>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

              <section className="executive-card">
                <div className="executive-card-heading">
                  <div>
                    <span className="executive-eyebrow">INTELLIGENT ALERTS</span>
                    <h2>Alert Center</h2>
                  </div>
                  <Link href="/monitoring#alert-center">Quản lý →</Link>
                </div>
                {overview.intelligent_alerts.length === 0 ? (
                  <p className="executive-empty">Không có cảnh báo đang hoạt động.</p>
                ) : (
                  <ul>
                    {overview.intelligent_alerts.slice(0, 10).map((alert) => (
                      <li key={alert.id}>
                        <strong>
                          {alert.severity} · {alert.title}
                        </strong>{" "}
                        ({alert.status}) — {alert.summary}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">ENTERPRISE WORKFLOW</span>
                  <h2>Tasks, deadlines and escalation</h2>
                </div>
                <Link href="/improvements">Mở workflow →</Link>
              </div>
              <p>{overview.workflow.escalation_policy}</p>
              {overview.workflow.active_cases.length === 0 ? (
                <p className="executive-empty">Chưa có công việc đang mở.</p>
              ) : (
                <div className="production-table-scroll">
                  <table className="production-table">
                    <thead>
                      <tr>
                        <th>Vấn đề</th>
                        <th>Hành động</th>
                        <th>Trạng thái</th>
                        <th>Owner</th>
                        <th>Deadline / escalation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.workflow.active_cases.slice(0, 20).map((item) => (
                        <tr key={item.id}>
                          <th scope="row">{item.problem}</th>
                          <td>{item.action ?? "Chưa lập hành động"}</td>
                          <td>{item.status} · {item.priority ?? "—"}</td>
                          <td>{item.owner_user_id ?? "Chưa giao"}</td>
                          <td>
                            {formatDate(item.action_due_at)} ·{" "}
                            {item.overdue
                              ? "QUÁ HẠN — escalation cần xử lý"
                              : item.escalation_status}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <div className="executive-two-column">
              <section className="executive-card">
                <div className="executive-card-heading">
                  <div>
                    <span className="executive-eyebrow">AUDIT & GOVERNANCE</span>
                    <h2>User activity</h2>
                  </div>
                  <Link href="/history">Analysis History →</Link>
                </div>
                <ul>
                  {overview.audit_governance.user_activity.map((activity) => (
                    <li key={activity.id}>
                      {formatDate(activity.created_at)} · {activity.action} ·{" "}
                      {activity.object_type} {activity.object_id ?? ""} ·{" "}
                      {activity.outcome}
                    </li>
                  ))}
                </ul>
                <h3>AI audit</h3>
                <p>
                  {overview.audit_governance.ai_analysis_audit.status === "available"
                    ? overview.audit_governance.ai_analysis_audit.by_analysis_and_status
                        ?.map(
                          (item) =>
                            `${item.analysis_type}/${item.status}: ${item.count}`,
                        )
                        .join(" · ") || "Không có AI request trong kỳ."
                    : "Chi tiết usage chỉ dành cho quản trị viên."}
                </p>
                <p>
                  Prompt/response nhạy cảm không hiển thị trong Enterprise Overview.
                </p>
              </section>

              <section className="executive-card">
                <div className="executive-card-heading">
                  <div>
                    <span className="executive-eyebrow">AI TRACEABILITY</span>
                    <h2>Algorithm, evidence and source lineage</h2>
                  </div>
                </div>
                <ul>
                  {overview.audit_governance.traceability.map((item) => (
                    <li key={`${item.domain}-${item.analysis_history_id}`}>
                      {domainLabels[item.domain] ?? item.domain} · history #
                      {item.analysis_history_id} ·{" "}
                      {item.algorithm_version ?? "version chưa ghi"} · AI{" "}
                      {item.ai_status ?? "—"} · {item.evidence_count} evidence
                      {item.source_file_sha256
                        ? ` · SHA-256 ${item.source_file_sha256.slice(0, 12)}…`
                        : ""}
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <section className="executive-card">
              <div className="executive-card-heading">
                <div>
                  <span className="executive-eyebrow">ENTERPRISE REPORTING</span>
                  <h2>Report readiness and governed publication</h2>
                </div>
                <Link href="/reports">Governed Reports →</Link>
              </div>
              <p>
                {overview.reporting.governed_report_count} báo cáo đã lưu. Các
                nguồn dưới đây có thể đưa vào quy trình tạo, review, approve,
                publish và export hiện có.
              </p>
              <div className="executive-ranking-list">
                {overview.reporting.report_templates.map((template) => (
                  <article key={template.key}>
                    <span className="executive-ranking-index">
                      {template.status === "ready"
                        ? "Sẵn sàng"
                        : template.status === "partial"
                          ? "Thiếu nguồn"
                          : "Chưa có nguồn"}
                    </span>
                    <span className="executive-ranking-name">
                      {template.title}
                    </span>
                    <span className="executive-ranking-metrics">
                      {template.cadence} · Analysis #{template.source_analysis_ids.join(", #") || "—"}
                      {template.missing_domains.length
                        ? ` · Thiếu: ${template.missing_domains.join(", ")}`
                        : ""}
                    </span>
                  </article>
                ))}
              </div>
              {overview.reporting.latest_governed_reports.length > 0 && (
                <p>
                  Báo cáo gần đây:{" "}
                  {overview.reporting.latest_governed_reports
                    .map(
                      (report) =>
                        `${report.title} v${report.current_version}`,
                    )
                    .join(" · ")}
                </p>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
