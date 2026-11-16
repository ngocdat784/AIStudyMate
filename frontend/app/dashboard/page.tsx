"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../../lib/api";
import { hasAuthToken } from "../../lib/auth";

type HistorySummary = {
  id: number;
  analysis_type: string;
  title: string | null;
  summary: string | null;
  source_filename: string | null;
  created_at: string | null;
};

type HistoryDetail = HistorySummary & {
  result_data: unknown;
};

type ImprovementSummary = {
  id: string;
  problem: string;
  status: string;
  updated_at: string;
};

type AlertSummary = {
  open_alerts: number;
  active_alerts: number;
  by_severity: Record<string, number>;
};

type DashboardData = {
  analysis: HistoryDetail | null;
  recentAnalyses: HistorySummary[];
  alerts: AlertSummary | null;
  improvements: ImprovementSummary[];
};

type Metric = {
  name: string;
  value: number;
  unit?: string;
};

type PerformanceRow = {
  name: string;
  production: number | null;
  defects: number | null;
  defectRate: number | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          typeof item === "object" && item !== null && !Array.isArray(item),
      )
    : [];
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricValue(metrics: Metric[], namePart: string): Metric | undefined {
  return metrics.find((metric) =>
    metric.name.toLocaleLowerCase("vi").includes(namePart.toLocaleLowerCase("vi")),
  );
}

function getMetrics(result: Record<string, unknown> | null): Metric[] {
  return asRecords(result?.kpis).flatMap((item) => {
    const value = finiteNumber(item.value);
    return typeof item.name === "string" && value !== null
      ? [
          {
            name: item.name,
            value,
            unit: typeof item.unit === "string" ? item.unit : undefined,
          },
        ]
      : [];
  });
}

function getPerformanceRows(
  result: Record<string, unknown> | null,
  key: "product_performance" | "supplier_performance",
): PerformanceRow[] {
  return asRecords(result?.[key])
    .map((item) => ({
      name:
        (typeof item.product === "string" && item.product) ||
        (typeof item.supplier === "string" && item.supplier) ||
        (typeof item.model === "string" && item.model) ||
        (typeof item.entity === "string" && item.entity) ||
        "Chưa xác định",
      production: finiteNumber(item.production),
      defects: finiteNumber(item.defects),
      defectRate: finiteNumber(item.defect_rate),
    }))
    .sort((left, right) => (right.defectRate ?? -1) - (left.defectRate ?? -1))
    .slice(0, 5);
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value == null
    ? "Chưa có dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "Thời gian chưa được ghi nhận";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("vi-VN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function formatDay(value: string): string {
      const date = new Date(`${value.slice(0, 10)}T12:00:00`);
      return Number.isNaN(date.getTime())
        ? value
        : new Intl.DateTimeFormat("vi-VN", {
            day: "2-digit",
            month: "2-digit",
          }).format(date);
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    detected: "Mới phát hiện",
    action_planned: "Đã lập kế hoạch",
    implemented: "Đã triển khai",
    measured: "Đã đo lường",
    evaluated: "Đã đánh giá",
    closed: "Đã đóng",
  };
  return labels[status] ?? status;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await apiFetch(url, { cache: "no-store" });
  if (!response.ok) {
    const requestId = response.headers.get("x-request-id");
    let detail = `Yêu cầu thất bại (HTTP ${response.status}).`;
    try {
      const body: unknown = await response.json();
      const record = asRecord(body);
      if (typeof record?.detail === "string") {
        detail = record.detail;
      }
    } catch {
      // Keep the HTTP status when the server does not return JSON.
    }
    throw new Error(requestId ? `${detail} Mã yêu cầu: ${requestId}` : detail);
  }
  return (await response.json()) as T;
}

function TrendChart({ result }: { result: Record<string, unknown> | null }) {
  const values = asRecords(result?.daily_production)
    .flatMap((item) => {
      const production = finiteNumber(item.production);
      return typeof item.date === "string" && production !== null
        ? [{ date: item.date, production }]
        : [];
    })
    .slice(-14);
  const maxProduction = Math.max(0, ...values.map((item) => item.production));

  if (values.length === 0) {
    return (
      <p className="executive-empty">
        Phân tích gần nhất chưa có dữ liệu theo ngày để thể hiện xu hướng.
      </p>
    );
  }

  return (
    <div
      className="executive-trend-chart"
      role="img"
      aria-label="Sản lượng theo ngày trong lần phân tích gần nhất"
    >
      {values.map((item) => {
        const height =
          maxProduction > 0
            ? Math.max(4, (item.production / maxProduction) * 100)
            : 4;
        return (
          <div
            className="executive-trend-column"
            key={`${item.date}-${item.production}`}
            title={`${item.date}: ${formatNumber(item.production, 0)} sản phẩm`}
          >
            <span className="executive-trend-value">
              {formatNumber(item.production, 0)}
            </span>
            <span
              className="executive-trend-bar"
              style={{ height: `${height}%` }}
            />
            <span className="executive-trend-date">{formatDay(item.date)}</span>
          </div>
        );
      })}
    </div>
  );
}

function PerformanceList({
  title,
  rows,
  emptyText,
}: {
  title: string;
  rows: PerformanceRow[];
  emptyText: string;
}) {
  return (
    <section className="executive-card">
      <div className="executive-card-heading">
        <div>
          <span className="executive-eyebrow">CẦN THEO DÕI</span>
          <h2>{title}</h2>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="executive-empty">{emptyText}</p>
      ) : (
        <ol className="executive-ranking-list">
          {rows.map((row, index) => (
            <li key={`${row.name}-${index}`}>
              <span className="executive-ranking-index">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="executive-ranking-name">{row.name}</span>
              <span className="executive-ranking-metrics">
                {row.defectRate == null
                  ? "Chưa có tỷ lệ lỗi"
                  : `${formatNumber(row.defectRate)}% lỗi`}
                {row.production != null &&
                  ` · ${formatNumber(row.production, 0)} sản phẩm`}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function ExecutiveDashboardPage() {
  const [data, setData] = useState<DashboardData>({
    analysis: null,
    recentAnalyses: [],
    alerts: null,
    improvements: [],
  });
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({});
  const [refreshKey, setRefreshKey] = useState(0);

  const loadDashboard = useCallback(async () => {
    if (!hasAuthToken()) {
      setAuthenticated(false);
      setLoading(false);
      return;
    }

    setAuthenticated(true);
    setLoading(true);
    setSectionErrors({});
    const [historyResult, alertResult, improvementResult] =
      await Promise.allSettled([
        getJson<{ items: HistorySummary[] }>("/api/history?page=1&page_size=5"),
        getJson<AlertSummary>("/api/monitoring/dashboard?days=30"),
        getJson<{ items: ImprovementSummary[] }>(
          "/api/improvements?page=1&page_size=5",
        ),
      ]);

    const errors: Record<string, string> = {};
    let recentAnalyses: HistorySummary[] = [];
    let alerts: AlertSummary | null = null;
    let improvements: ImprovementSummary[] = [];
    let analysis: HistoryDetail | null = null;

    if (historyResult.status === "fulfilled") {
      recentAnalyses = historyResult.value.items;
      const latest = recentAnalyses[0];
      if (latest) {
        try {
          analysis = await getJson<HistoryDetail>(`/api/history/${latest.id}`);
        } catch (error) {
          errors.analysis =
            error instanceof Error
              ? error.message
              : "Không thể tải dữ liệu phân tích gần nhất.";
        }
      }
    } else {
      errors.analysis =
        historyResult.reason instanceof Error
          ? historyResult.reason.message
          : "Không thể tải lịch sử phân tích.";
    }

    if (alertResult.status === "fulfilled") {
      alerts = alertResult.value;
    } else {
      errors.alerts =
        alertResult.reason instanceof Error
          ? alertResult.reason.message
          : "Không thể tải trạng thái cảnh báo.";
    }

    if (improvementResult.status === "fulfilled") {
      improvements = improvementResult.value.items;
    } else {
      errors.improvements =
        improvementResult.reason instanceof Error
          ? improvementResult.reason.message
          : "Không thể tải hồ sơ cải tiến.";
    }

    setData({ analysis, recentAnalyses, alerts, improvements });
    setSectionErrors(errors);
    setLoading(false);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadDashboard(), 0);
    return () => window.clearTimeout(timer);
  }, [loadDashboard, refreshKey]);

  const result = asRecord(data.analysis?.result_data);
  const metrics = getMetrics(result);
  const production = metricValue(metrics, "tổng sản lượng");
  const defectRate = metricValue(metrics, "tỷ lệ lỗi");
  const downtime = metricValue(metrics, "tổng downtime");
  const products = getPerformanceRows(result, "product_performance");
  const suppliers = getPerformanceRows(result, "supplier_performance");

  if (!authenticated && !loading) {
    return (
      <main className="executive-page">
        <section className="executive-access-card">
          <span className="executive-eyebrow">HARDWAREAI · ĐIỀU HÀNH</span>
          <h1>Đăng nhập để xem tổng quan sản xuất</h1>
          <p>
            Dashboard chỉ hiển thị dữ liệu trong phạm vi tài khoản được cấp.
          </p>
          <Link className="executive-primary-link" href="/monitoring">
            Đăng nhập hoặc chọn phạm vi truy cập
          </Link>
        </section>
      </main>
    );
  }

  return (
    <main className="executive-page">
      <div className="executive-content">
        <header className="executive-header">
          <div>
            <span className="executive-eyebrow">HARDWAREAI · ĐIỀU HÀNH</span>
            <h1>Tổng quan sản xuất</h1>
            <p>
              Theo dõi nhanh kết quả phân tích, cảnh báo và hành động cải tiến
              trong phạm vi tài khoản của bạn.
            </p>
          </div>
          <button
            className="executive-refresh"
            type="button"
            onClick={() => setRefreshKey((key) => key + 1)}
            disabled={loading}
          >
            {loading ? "Đang cập nhật…" : "Làm mới"}
          </button>
        </header>

        {loading && (
          <p className="executive-loading" role="status">
            Đang tải dữ liệu vận hành…
          </p>
        )}

        <section className="executive-kpi-grid" aria-label="Chỉ số chính">
          <Link
            className="executive-kpi-card"
            href={
              data.analysis
                ? `/history?history_id=${data.analysis.id}`
                : "/history"
            }
          >
            <span>Tổng sản lượng · phân tích gần nhất</span>
            <strong>{formatNumber(production?.value, 0)}</strong>
            <small>{production?.unit ?? "sản phẩm"}</small>
          </Link>
          <Link
            className="executive-kpi-card"
            href={
              data.analysis
                ? `/history?history_id=${data.analysis.id}`
                : "/history"
            }
          >
            <span>Tỷ lệ lỗi · phân tích gần nhất</span>
            <strong>
              {defectRate ? `${formatNumber(defectRate.value)}%` : "Chưa có dữ liệu"}
            </strong>
            <small>Python tính từ dữ liệu phân tích</small>
          </Link>
          <Link
            className="executive-kpi-card"
            href={
              data.analysis
                ? `/history?history_id=${data.analysis.id}`
                : "/history"
            }
          >
            <span>Downtime · phân tích gần nhất</span>
            <strong>{formatNumber(downtime?.value, 0)}</strong>
            <small>{downtime?.unit ?? "phút"}</small>
          </Link>
          <Link className="executive-kpi-card executive-kpi-alert" href="/monitoring#alert-center">
            <span>Cảnh báo đang mở</span>
            <strong>
              {data.alerts ? data.alerts.open_alerts.toLocaleString("vi-VN") : "—"}
            </strong>
            <small>
              {data.alerts
                ? `${data.alerts.active_alerts.toLocaleString("vi-VN")} cảnh báo đang hoạt động`
                : "Chưa tải được trạng thái"}
            </small>
          </Link>
        </section>

        {sectionErrors.analysis && (
          <p className="executive-error" role="alert">
            Dữ liệu phân tích: {sectionErrors.analysis}
          </p>
        )}
        {sectionErrors.alerts && (
          <p className="executive-error" role="alert">
            Cảnh báo: {sectionErrors.alerts}
          </p>
        )}
        {sectionErrors.improvements && (
          <p className="executive-error" role="alert">
            Cải tiến: {sectionErrors.improvements}
          </p>
        )}

        <section className="executive-card executive-trend-card">
          <div className="executive-card-heading">
            <div>
              <span className="executive-eyebrow">XU HƯỚNG SẢN XUẤT</span>
              <h2>Theo ngày trong lần phân tích gần nhất</h2>
            </div>
            {data.analysis && (
              <Link href={`/history?history_id=${data.analysis.id}`}>
                Xem dữ liệu và bằng chứng →
              </Link>
            )}
          </div>
          <p className="executive-data-context">
            {data.analysis
              ? `${data.analysis.source_filename ?? data.analysis.title ?? "Tập dữ liệu"} · ${formatDate(data.analysis.created_at)}`
              : "Chưa có phân tích được lưu trong phạm vi tài khoản."}
          </p>
          <TrendChart result={result} />
          {!data.analysis && !loading && (
            <div className="executive-empty-action">
              <p>
                Tải dữ liệu CSV/XLSX và chạy phân tích để bắt đầu theo dõi KPI.
              </p>
              <Link className="executive-primary-link" href="/">
                Mở phân tích dữ liệu
              </Link>
            </div>
          )}
        </section>

        <div className="executive-two-column">
          <PerformanceList
            title="Sản phẩm có tỷ lệ lỗi cao"
            rows={products}
            emptyText="Chưa có phân tích chứa dữ liệu sản phẩm."
          />
          <PerformanceList
            title="Nhà cung cấp cần xem xét"
            rows={suppliers}
            emptyText="Chưa có phân tích chứa dữ liệu nhà cung cấp."
          />
        </div>

        <div className="executive-two-column">
          <section className="executive-card">
            <div className="executive-card-heading">
              <div>
                <span className="executive-eyebrow">CẢNH BÁO</span>
                <h2>Ưu tiên xử lý</h2>
              </div>
              <Link href="/monitoring#alert-center">Mở Alert Center →</Link>
            </div>
            {data.alerts ? (
              <ul className="executive-severity-list">
                {[
                  ["critical", "Nghiêm trọng"],
                  ["high", "Cao"],
                  ["medium", "Trung bình"],
                  ["low", "Thấp"],
                ].map(([severity, label]) => (
                  <li key={severity}>
                    <span>{label}</span>
                    <strong>
                      {(data.alerts?.by_severity[severity] ?? 0).toLocaleString(
                        "vi-VN",
                      )}
                    </strong>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="executive-empty">
                Chưa có dữ liệu cảnh báo hoặc không thể tải dữ liệu.
              </p>
            )}
          </section>

          <section className="executive-card">
            <div className="executive-card-heading">
              <div>
                <span className="executive-eyebrow">CẢI TIẾN LIÊN TỤC</span>
                <h2>Hoạt động gần đây</h2>
              </div>
              <Link href="/improvements">Mở hồ sơ cải tiến →</Link>
            </div>
            {data.improvements.length === 0 ? (
              <p className="executive-empty">
                Chưa có hồ sơ cải tiến nào trong phạm vi truy cập.
              </p>
            ) : (
              <ul className="executive-activity-list">
                {data.improvements.map((item) => (
                  <li key={item.id}>
                    <Link href="/improvements">{item.problem}</Link>
                    <span>
                      {statusLabel(item.status)} · {formatDate(item.updated_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <section className="executive-card">
          <div className="executive-card-heading">
            <div>
              <span className="executive-eyebrow">DATA LINEAGE</span>
              <h2>Phân tích gần đây</h2>
            </div>
            <Link href="/history">Toàn bộ lịch sử →</Link>
          </div>
          {data.recentAnalyses.length === 0 ? (
            <p className="executive-empty">
              Chưa có hoạt động phân tích. Hãy bắt đầu bằng dữ liệu sản xuất của
              bạn.
            </p>
          ) : (
            <ul className="executive-activity-list">
              {data.recentAnalyses.map((item) => (
                <li key={item.id}>
                  <Link href={`/history?history_id=${item.id}`}>
                    {item.title ?? item.source_filename ?? "Phân tích sản xuất"}
                  </Link>
                  <span>{formatDate(item.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <footer className="executive-footnote">
          KPI sản lượng, tỷ lệ lỗi, downtime và thứ hạng được lấy từ lần phân
          tích được lưu gần nhất, không phải tổng hợp toàn nhà máy. Cảnh báo
          được đếm riêng trong phạm vi Plant được cấp quyền. Mối liên hệ theo
          thời gian không tự chứng minh quan hệ nhân quả.
        </footer>
      </div>
    </main>
  );
}
