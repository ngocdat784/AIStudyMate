"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import "./history.css";

const API_BASE_URL = "http://127.0.0.1:8000/api/history";
const PAGE_SIZE = 10;

type AnalysisType =
  | "hardware_analysis"
  | "hardware_comparison"
  | "hardware_trends"
  | string;

type HistorySummary = {
  id: number;
  analysis_type: AnalysisType;
  title: string | null;
  summary: string | null;
  source_filename: string | null;
  dataset_id: string | null;
  created_at: string | null;
};

type HistoryDetail = HistorySummary & {
  result_data: Record<string, unknown>;
};

type HistoryListResponse = {
  items: HistorySummary[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
};

type HistoryTypeFilter =
  | ""
  | "hardware_analysis"
  | "hardware_comparison"
  | "hardware_trends";

async function getApiError(response: Response): Promise<string> {
  const responseText = await response.text();
  if (!responseText) {
    return `Yêu cầu thất bại (HTTP ${response.status}).`;
  }

  try {
    const data: unknown = JSON.parse(responseText);
    if (
      typeof data === "object" &&
      data !== null &&
      "detail" in data &&
      typeof data.detail === "string"
    ) {
      return data.detail;
    }
  } catch {
    return responseText;
  }

  return `Yêu cầu thất bại (HTTP ${response.status}).`;
}

function analysisTypeLabel(type: string): string {
  switch (type) {
    case "hardware_analysis":
      return "Phân tích phần cứng";
    case "hardware_comparison":
      return "So sánh model";
    case "hardware_trends":
      return "Phân tích xu hướng";
    default:
      return type;
  }
}

function formatDate(value: string | null): string {
  if (!value) {
    return "Không có thời gian";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function asRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is Record<string, unknown> =>
      typeof item === "object" && item !== null && !Array.isArray(item)
  );
}

function asStrings(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "number") {
    return value.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function ResultSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="history-result-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function HistoryResult({ detail }: { detail: HistoryDetail }) {
  const result = detail.result_data;
  const kpis = asRecords(result.kpis);
  const insights = asStrings(result.insights);
  const warnings = asStrings(result.warnings);
  const recommendations = asStrings(result.recommendations);
  const aiAnalysis =
    typeof result.ai_analysis === "object" && result.ai_analysis !== null
      ? (result.ai_analysis as Record<string, unknown>)
      : null;

  return (
    <div className="history-detail-result">
      {kpis.length > 0 && (
        <ResultSection title="KPI Overview">
          <div className="history-kpi-grid">
            {kpis.map((kpi, index) => (
              <article
                className="history-kpi"
                key={`${String(kpi.name ?? "kpi")}-${index}`}
              >
                <span>{displayValue(kpi.name)}</span>
                <strong>
                  {displayValue(kpi.value)} {displayValue(kpi.unit)}
                </strong>
                {typeof kpi.description === "string" && (
                  <small>{kpi.description}</small>
                )}
              </article>
            ))}
          </div>
        </ResultSection>
      )}

      {insights.length > 0 && (
        <ResultSection title="Insights">
          <ul className="history-bullet-list">
            {insights.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </ResultSection>
      )}

      {aiAnalysis && (
        <ResultSection title="AI Analysis">
          <pre className="history-json">
            {JSON.stringify(aiAnalysis, null, 2)}
          </pre>
        </ResultSection>
      )}

      {warnings.length > 0 && (
        <ResultSection title="Warnings">
          <ul className="history-bullet-list">
            {warnings.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </ResultSection>
      )}

      {recommendations.length > 0 && (
        <ResultSection title="Recommendations">
          <ul className="history-bullet-list">
            {recommendations.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </ResultSection>
      )}

      <details className="history-raw-data">
        <summary>Xem toàn bộ dữ liệu kết quả</summary>
        <pre className="history-json">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistorySummary[]>([]);
  const [page, setPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [analysisType, setAnalysisType] = useState<HistoryTypeFilter>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedHistory, setSelectedHistory] =
    useState<HistoryDetail | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<number | null>(null);
  const [deleteLoadingId, setDeleteLoadingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (analysisType) {
      params.set("analysis_type", analysisType);
    }

    const fetchHistory = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}?${params.toString()}`, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(await getApiError(response));
        }
        const data = (await response.json()) as HistoryListResponse;
        if (active) {
          setItems(data.items);
          setTotal(data.pagination.total);
          setTotalPages(data.pagination.total_pages);
          setError("");
        }
      } catch (loadError) {
        console.error("Could not load analysis history:", loadError);
        if (active) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Không thể tải lịch sử phân tích."
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    void fetchHistory();
    return () => {
      active = false;
    };
  }, [analysisType, page, refreshKey]);

  const reloadHistory = () => {
    setLoading(true);
    setError("");
    setRefreshKey((currentKey) => currentKey + 1);
  };

  const handleFilterChange = (value: HistoryTypeFilter) => {
    setLoading(true);
    setAnalysisType(value);
    setPage(1);
    setError("");
    setSelectedHistory(null);
    setActionError("");
    setNotice("");
  };

  const handleOpenDetail = async (item: HistorySummary) => {
    setDetailLoadingId(item.id);
    setActionError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE_URL}/${item.id}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(await getApiError(response));
      }
      setSelectedHistory((await response.json()) as HistoryDetail);
    } catch (detailError) {
      console.error("Could not load analysis history detail:", detailError);
      setActionError(
        detailError instanceof Error
          ? detailError.message
          : "Không thể tải chi tiết phân tích."
      );
    } finally {
      setDetailLoadingId(null);
    }
  };

  const handleDelete = async (item: HistorySummary) => {
    const confirmed = window.confirm(
      `Bạn có chắc muốn xóa lịch sử “${item.title ?? analysisTypeLabel(item.analysis_type)}”?`
    );
    if (!confirmed) {
      return;
    }

    setDeleteLoadingId(item.id);
    setActionError("");
    setNotice("");
    try {
      const response = await fetch(`${API_BASE_URL}/${item.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await getApiError(response));
      }

      if (selectedHistory?.id === item.id) {
        setSelectedHistory(null);
      }
      setNotice("Đã xóa bản ghi lịch sử.");
      if (items.length === 1 && page > 1) {
        setLoading(true);
        setPage((currentPage) => currentPage - 1);
      } else {
        reloadHistory();
      }
    } catch (deleteError) {
      console.error("Could not delete analysis history:", deleteError);
      setActionError(
        deleteError instanceof Error
          ? deleteError.message
          : "Không thể xóa bản ghi lịch sử."
      );
    } finally {
      setDeleteLoadingId(null);
    }
  };

  return (
    <main className="history-page">
      <div className="history-glow history-glow-one" />
      <div className="history-glow history-glow-two" />

      <div className="history-container">
        <header className="history-topbar">
          <Link className="history-brand" href="/">
            <span className="history-brand-mark">HW</span>
            <span>
              <strong>HardwareAI</strong>
              <small>Analysis history</small>
            </span>
          </Link>
          <Link className="history-back-link" href="/">
            <span aria-hidden="true">←</span> Về trang phân tích
          </Link>
        </header>

        <section className="history-hero">
          <span className="history-eyebrow">HISTORY CENTER</span>
          <h1>Lịch sử phân tích</h1>
          <p>
            Xem lại kết quả đã lưu, mở chi tiết hoặc xóa những bản ghi không
            còn cần thiết.
          </p>
        </section>

        <section className="history-panel" aria-labelledby="history-list-title">
          <div className="history-panel-heading">
            <div>
              <span className="history-section-index">01 / ARCHIVE</span>
              <h2 id="history-list-title">Các lần phân tích</h2>
              <p>
                Danh sách hiển thị thông tin tóm tắt. Chọn một bản ghi để tải
                dữ liệu kết quả đầy đủ.
              </p>
            </div>
            <button
              className="history-refresh-button"
              type="button"
              onClick={reloadHistory}
              disabled={loading}
            >
              {loading ? "Đang tải..." : "↻ Làm mới"}
            </button>
          </div>

          <div className="history-toolbar">
            <label className="history-filter">
              <span>Loại phân tích</span>
              <select
                value={analysisType}
                onChange={(event) =>
                  handleFilterChange(event.target.value as HistoryTypeFilter)
                }
              >
                <option value="">Tất cả loại</option>
                <option value="hardware_analysis">
                  Phân tích phần cứng
                </option>
                <option value="hardware_comparison">So sánh model</option>
                <option value="hardware_trends">Phân tích xu hướng</option>
              </select>
            </label>
            <div className="history-total">
              <strong>{total.toLocaleString("vi-VN")}</strong>
              <span>bản ghi</span>
            </div>
          </div>

          {error && (
            <div className="history-alert history-alert-error" role="alert">
              <strong>Không thể tải lịch sử</strong>
              <p>{error}</p>
              <button type="button" onClick={reloadHistory}>
                Thử lại
              </button>
            </div>
          )}

          {actionError && (
            <div className="history-alert history-alert-error" role="alert">
              {actionError}
            </div>
          )}
          {notice && (
            <div className="history-alert history-alert-success" role="status">
              {notice}
            </div>
          )}

          {loading && items.length === 0 ? (
            <div className="history-state" role="status">
              <span className="history-loader" />
              Đang tải lịch sử phân tích...
            </div>
          ) : !error && items.length === 0 ? (
            <div className="history-state history-empty">
              <span className="history-empty-icon">∅</span>
              <strong>Chưa có lịch sử phù hợp</strong>
              <p>
                Khi có kết quả phân tích được lưu ở backend, chúng sẽ xuất hiện
                tại đây.
              </p>
            </div>
          ) : (
            <div className="history-table-wrap">
              <table className="history-table">
                <thead>
                  <tr>
                    <th>Phân tích</th>
                    <th>Ngày tạo</th>
                    <th>Nguồn dữ liệu</th>
                    <th>Tóm tắt</th>
                    <th className="history-actions-heading">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <span className="history-type-badge">
                          {analysisTypeLabel(item.analysis_type)}
                        </span>
                        <strong className="history-title">
                          {item.title ?? "Không có tiêu đề"}
                        </strong>
                        <small className="history-id">ID: {item.id}</small>
                      </td>
                      <td className="history-date">
                        {formatDate(item.created_at)}
                      </td>
                      <td className="history-source">
                        {item.source_filename ?? (
                          <span className="history-muted">
                            {item.dataset_id
                              ? `Dataset ${item.dataset_id}`
                              : "—"}
                          </span>
                        )}
                      </td>
                      <td className="history-summary">
                        {item.summary ?? "Không có tóm tắt."}
                      </td>
                      <td>
                        <div className="history-row-actions">
                          <button
                            className="history-detail-button"
                            type="button"
                            onClick={() => void handleOpenDetail(item)}
                            disabled={
                              detailLoadingId !== null ||
                              deleteLoadingId !== null
                            }
                          >
                            {detailLoadingId === item.id
                              ? "Đang mở..."
                              : "Chi tiết"}
                          </button>
                          <button
                            className="history-delete-button"
                            type="button"
                            onClick={() => void handleDelete(item)}
                            disabled={
                              deleteLoadingId !== null ||
                              detailLoadingId !== null
                            }
                          >
                            {deleteLoadingId === item.id
                              ? "Đang xóa..."
                              : "Xóa"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loading && totalPages > 0 && (
            <nav className="history-pagination" aria-label="Phân trang lịch sử">
              <span>
                Trang {page} / {totalPages}
              </span>
              <div>
                <button
                  type="button"
                  onClick={() => {
                    setLoading(true);
                    setPage((currentPage) => currentPage - 1);
                  }}
                  disabled={page <= 1}
                >
                  ← Trang trước
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLoading(true);
                    setPage((currentPage) => currentPage + 1);
                  }}
                  disabled={page >= totalPages}
                >
                  Trang sau →
                </button>
              </div>
            </nav>
          )}
        </section>

        {selectedHistory && (
          <section
            className="history-detail-panel"
            aria-labelledby="history-detail-title"
          >
            <div className="history-panel-heading history-detail-heading">
              <div>
                <span className="history-section-index">02 / RECORD DETAIL</span>
                <h2 id="history-detail-title">
                  {selectedHistory.title ?? "Chi tiết phân tích"}
                </h2>
                <p>
                  {analysisTypeLabel(selectedHistory.analysis_type)} ·{" "}
                  {formatDate(selectedHistory.created_at)}
                </p>
              </div>
              <button
                className="history-close-button"
                type="button"
                onClick={() => setSelectedHistory(null)}
                aria-label="Đóng chi tiết"
              >
                ×
              </button>
            </div>
            <div className="history-detail-meta">
              <div>
                <span>File nguồn</span>
                <strong>{selectedHistory.source_filename ?? "—"}</strong>
              </div>
              <div>
                <span>Dataset ID</span>
                <strong>{selectedHistory.dataset_id ?? "—"}</strong>
              </div>
              <div>
                <span>ID lịch sử</span>
                <strong>{selectedHistory.id}</strong>
              </div>
            </div>
            {selectedHistory.summary && (
              <p className="history-detail-summary">{selectedHistory.summary}</p>
            )}
            <HistoryResult detail={selectedHistory} />
          </section>
        )}

        <footer className="history-footer">
          Lịch sử được truy xuất từ API backend. Bản ghi chỉ bị xóa khi bạn xác
          nhận thao tác.
        </footer>
      </div>
    </main>
  );
}
