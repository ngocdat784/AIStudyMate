"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../lib/api";
import { hasAuthToken, redirectToLogin } from "../../lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

type Plant = { id: number; name: string };
type Observation = {
  value: number;
  sample_count: number;
  standard_deviation?: number | null;
};
type ImprovementCase = {
  id: string;
  plant_id: number;
  source_alert_id: number | null;
  owner_user_id: number | null;
  problem: string;
  baseline: Record<string, Observation>;
  root_cause: string | null;
  action: string | null;
  implemented_at: string | null;
  after_measurement: Record<string, Observation> | null;
  kpi_changes: Record<
    string,
    {
      baseline: number;
      after: number;
      absolute_change: number;
      relative_change_percent: number | null;
    }
  >;
  confounders: string[];
  evidence_confidence: string;
  confidence_basis: string;
  conclusion: string | null;
  causality_notice: string;
  status: string;
  created_at: string;
};

const STATUS_LABELS: Record<string, string> = {
  detected: "Phát hiện",
  action_planned: "Đã lập kế hoạch",
  implemented: "Đã triển khai",
  measured: "Đã đo lường",
  evaluated: "Đã đánh giá",
  closed: "Đã đóng",
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API_URL}${path}`, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Yêu cầu thất bại (HTTP ${response.status}).`,
    );
  }
  return payload as T;
}

function localDateTimeValue() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export default function ImprovementsPage() {
  const [plants, setPlants] = useState<Plant[]>([]);
  const [cases, setCases] = useState<ImprovementCase[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState("");
  const [plantId, setPlantId] = useState("");
  const [sourceAlertId, setSourceAlertId] = useState("");
  const [problem, setProblem] = useState("");
  const [metricName, setMetricName] = useState("defect_rate");
  const [baselineValue, setBaselineValue] = useState("");
  const [baselineSamples, setBaselineSamples] = useState("30");
  const [rootCause, setRootCause] = useState("");
  const [action, setAction] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [implementedAt, setImplementedAt] = useState(localDateTimeValue);
  const [afterValue, setAfterValue] = useState("");
  const [afterSamples, setAfterSamples] = useState("30");
  const [afterStdDev, setAfterStdDev] = useState("");
  const [confounders, setConfounders] = useState("");
  const [conclusion, setConclusion] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedCase = useMemo(
    () => cases.find((item) => item.id === selectedId) ?? null,
    [cases, selectedId],
  );
  const selectedPlantName =
    plants.find((item) => String(item.id) === String(selectedCase?.plant_id))
      ?.name ?? "Nhà máy";

  const refresh = useCallback(async (pageNumber = page) => {
    const [plantData, caseData] = await Promise.all([
      requestJson<Plant[]>("/api/monitoring/plants"),
      requestJson<{ items: ImprovementCase[]; total: number }>(
        `/api/improvements?page=${pageNumber}&page_size=20`,
      ),
    ]);
    setPlants(plantData);
    setCases(caseData.items);
    setTotal(caseData.total);
  }, [page]);

  useEffect(() => {
    if (!hasAuthToken()) {
      redirectToLogin();
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      void refresh()
        .catch((cause: unknown) => {
          if (active) {
            setError(
              cause instanceof Error
                ? cause.message
                : "Không thể tải hồ sơ cải tiến.",
            );
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [refresh]);

  async function createCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plantId) {
      setError("Hãy chọn nhà máy để tạo hồ sơ.");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const created = await requestJson<ImprovementCase>("/api/improvements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plant_id: Number(plantId),
          ...(sourceAlertId ? { source_alert_id: Number(sourceAlertId) } : {}),
          problem,
          baseline: {
            [metricName.trim()]: {
              value: Number(baselineValue),
              sample_count: Number(baselineSamples),
            },
          },
        }),
      });
      if (page === 1) {
        await refresh(1);
      } else {
        setPage(1);
      }
      setSelectedId(created.id);
      setProblem("");
      setBaselineValue("");
      setMessage("Đã tạo hồ sơ và ghi nhận KPI nền.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể tạo hồ sơ.");
    } finally {
      setBusy(false);
    }
  }

  async function updateCase(
    data: Record<string, unknown>,
    successMessage: string,
  ) {
    if (!selectedCase) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await requestJson<ImprovementCase>(
        `/api/improvements/${selectedCase.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
      );
      await refresh();
      setSelectedId(updated.id);
      setMessage(successMessage);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Không thể cập nhật hồ sơ.",
      );
    } finally {
      setBusy(false);
    }
  }

  function planAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void updateCase(
      {
        root_cause: rootCause,
        action,
        owner_user_id: ownerId ? Number(ownerId) : undefined,
      },
      "Đã lưu nguyên nhân, hành động và người phụ trách.",
    );
  }

  function submitMeasurement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCase) return;
    const metric = Object.keys(selectedCase.baseline)[0];
    if (!metric) return;
    void updateCase(
      {
        implemented_at: new Date(implementedAt).toISOString(),
        after_measurement: {
          [metric]: {
            value: Number(afterValue),
            sample_count: Number(afterSamples),
            ...(afterStdDev ? { standard_deviation: Number(afterStdDev) } : {}),
          },
        },
        confounders: confounders
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      },
      "Đã ghi nhận thời điểm triển khai và số liệu sau cải tiến.",
    );
  }

  return (
    <main className="integration-page">
      <header className="integration-hero">
        <div>
          <span className="integration-eyebrow">CONTINUOUS IMPROVEMENT</span>
          <h1>Theo dõi hiệu quả cải tiến</h1>
          <p>
            Nối vấn đề, KPI nền, hành động và kết quả sau can thiệp trong một hồ
            sơ có thể truy xuất. Mức thay đổi quan sát được không tự chứng minh
            quan hệ nhân quả.
          </p>
        </div>
        <span className="integration-hero-status">
          {total} hồ sơ trong phạm vi truy cập
        </span>
      </header>

      {error && (
        <div className="integration-notice error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="integration-notice success" role="status">
          {message}
        </div>
      )}

      <div className="integration-setup-grid">
        <section className="integration-panel">
          <div className="integration-panel-heading">
            <div>
              <span className="integration-eyebrow">BẮT ĐẦU</span>
              <h3>Tạo hồ sơ cải tiến</h3>
              <p>Ghi nhận vấn đề và KPI trước khi can thiệp.</p>
            </div>
          </div>
          <form onSubmit={createCase}>
            <label>
              Nhà máy
              <select
                required
                value={plantId}
                onChange={(event) => setPlantId(event.target.value)}
              >
                <option value="">Chọn nhà máy</option>
                {plants.map((plant) => (
                  <option key={plant.id} value={plant.id}>
                    {plant.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Vấn đề cần cải tiến
              <textarea
                required
                maxLength={4000}
                value={problem}
                onChange={(event) => setProblem(event.target.value)}
                placeholder="Mô tả vấn đề dựa trên bằng chứng hoặc cảnh báo"
              />
            </label>
            <label>
              ID cảnh báo nguồn (không bắt buộc)
              <input
                min="1"
                type="number"
                value={sourceAlertId}
                onChange={(event) => setSourceAlertId(event.target.value)}
              />
            </label>
            <div className="integration-form-row">
              <label>
                Tên KPI
                <input
                  required
                  value={metricName}
                  onChange={(event) => setMetricName(event.target.value)}
                  placeholder="defect_rate"
                />
              </label>
              <label>
                Giá trị nền
                <input
                  required
                  type="number"
                  step="any"
                  value={baselineValue}
                  onChange={(event) => setBaselineValue(event.target.value)}
                />
              </label>
            </div>
            <label>
              Số mẫu trước can thiệp
              <input
                required
                min="1"
                type="number"
                value={baselineSamples}
                onChange={(event) => setBaselineSamples(event.target.value)}
              />
            </label>
            <div className="integration-actions">
              <button
                className="integration-button primary"
                type="submit"
                disabled={busy || plants.length === 0}
              >
                {busy ? "Đang lưu…" : "Tạo hồ sơ"}
              </button>
            </div>
          </form>
          {plants.length === 0 && !loading && (
            <p className="integration-empty">
              Chưa có nhà máy trong phạm vi truy cập để tạo hồ sơ.
            </p>
          )}
        </section>

        <section className="integration-panel">
          <div className="integration-panel-heading">
            <div>
              <span className="integration-eyebrow">VÒNG ĐỜI</span>
              <h3>{selectedCase ? selectedCase.problem : "Chọn một hồ sơ"}</h3>
              <p>
                {selectedCase
                  ? `${selectedPlantName} · ${STATUS_LABELS[selectedCase.status] ?? selectedCase.status}`
                  : "Chọn hồ sơ ở danh sách để tiếp tục kế hoạch và đo lường."}
              </p>
            </div>
          </div>
          {selectedCase ? (
            <div className="improvement-workflow">
              <div className="improvement-kpi-grid">
                {Object.entries(selectedCase.baseline).map(([name, value]) => {
                  const change = selectedCase.kpi_changes[name];
                  return (
                    <article className="improvement-kpi" key={name}>
                      <span>{name}</span>
                      <strong>
                        {value.value} → {change?.after ?? "—"}
                      </strong>
                      <small>
                        {change
                          ? `${change.absolute_change > 0 ? "+" : ""}${change.absolute_change} (${change.relative_change_percent === null ? "—" : `${change.relative_change_percent.toFixed(1)}%`})`
                          : `Baseline · ${value.sample_count} mẫu`}
                      </small>
                    </article>
                  );
                })}
              </div>
              {selectedCase.status === "detected" && (
                <form onSubmit={planAction}>
                  <label>
                    Nguyên nhân gốc / giả thuyết
                    <textarea
                      required
                      value={rootCause}
                      onChange={(event) => setRootCause(event.target.value)}
                    />
                  </label>
                  <label>
                    Hành động cải tiến
                    <textarea
                      required
                      value={action}
                      onChange={(event) => setAction(event.target.value)}
                    />
                  </label>
                  <label>
                    ID người phụ trách (để trống để chưa giao)
                    <input
                      min="1"
                      type="number"
                      value={ownerId}
                      onChange={(event) => setOwnerId(event.target.value)}
                    />
                  </label>
                  <div className="integration-actions">
                    <button
                      className="integration-button primary"
                      type="submit"
                      disabled={busy}
                    >
                      Lưu kế hoạch
                    </button>
                  </div>
                </form>
              )}
              {selectedCase.status === "action_planned" && (
                <form onSubmit={submitMeasurement}>
                  <label>
                    Thời điểm triển khai
                    <input
                      required
                      type="datetime-local"
                      value={implementedAt}
                      onChange={(event) => setImplementedAt(event.target.value)}
                    />
                  </label>
                  <label>
                    Giá trị KPI sau can thiệp
                    <input
                      required
                      type="number"
                      step="any"
                      value={afterValue}
                      onChange={(event) => setAfterValue(event.target.value)}
                    />
                  </label>
                  <div className="integration-form-row">
                    <label>
                      Số mẫu sau can thiệp
                      <input
                        required
                        min="1"
                        type="number"
                        value={afterSamples}
                        onChange={(event) => setAfterSamples(event.target.value)}
                      />
                    </label>
                    <label>
                      Độ lệch chuẩn (nếu có)
                      <input
                        min="0"
                        type="number"
                        step="any"
                        value={afterStdDev}
                        onChange={(event) => setAfterStdDev(event.target.value)}
                      />
                    </label>
                  </div>
                  <label>
                    Yếu tố gây nhiễu đã xem xét (phân cách bằng dấu phẩy)
                    <input
                      value={confounders}
                      onChange={(event) => setConfounders(event.target.value)}
                    />
                  </label>
                  <div className="integration-actions">
                    <button
                      className="integration-button primary"
                      type="submit"
                      disabled={busy}
                    >
                      Lưu đo lường
                    </button>
                  </div>
                </form>
              )}
              {selectedCase.status === "measured" && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void updateCase(
                      { conclusion },
                      "Đã đánh giá kết quả quan sát.",
                    );
                  }}
                >
                  <p className="improvement-causality">
                    {selectedCase.causality_notice} Độ tin cậy tài liệu:{" "}
                    {selectedCase.evidence_confidence}.{" "}
                    {selectedCase.confidence_basis}
                  </p>
                  <label>
                    Kết luận và bài học
                    <textarea
                      required
                      value={conclusion}
                      onChange={(event) => setConclusion(event.target.value)}
                    />
                  </label>
                  <div className="integration-actions">
                    <button
                      className="integration-button primary"
                      type="submit"
                      disabled={busy}
                    >
                      Đánh giá kết quả
                    </button>
                  </div>
                </form>
              )}
              {selectedCase.status === "evaluated" && (
                <div className="improvement-evaluated">
                  <p>{selectedCase.conclusion}</p>
                  <p className="improvement-causality">
                    {selectedCase.causality_notice} Độ tin cậy tài liệu:{" "}
                    {selectedCase.evidence_confidence}.{" "}
                    {selectedCase.confidence_basis}
                  </p>
                  <button
                    className="integration-button secondary"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void updateCase({ close: true }, "Đã đóng hồ sơ cải tiến.")
                    }
                  >
                    Đóng hồ sơ
                  </button>
                </div>
              )}
              {selectedCase.status === "closed" && (
                <p className="improvement-causality">
                  Hồ sơ đã đóng. Kết luận: {selectedCase.conclusion}
                </p>
              )}
            </div>
          ) : (
            <div className="integration-empty">
              Mỗi hồ sơ giữ lại baseline, hành động, người phụ trách và phép đo
              sau can thiệp để tiện rà soát.
            </div>
          )}
        </section>
      </div>

      <section className="integration-panel">
        <div className="integration-section-heading">
          <div>
            <span className="integration-eyebrow">THEO DÕI</span>
            <h2>Hồ sơ cải tiến</h2>
          </div>
          <span className="integration-count">{cases.length} hồ sơ</span>
        </div>
        {loading ? (
          <p className="integration-empty" role="status">
            Đang tải hồ sơ…
          </p>
        ) : cases.length === 0 ? (
          <p className="integration-empty">
            Chưa có hồ sơ cải tiến. Bắt đầu bằng việc ghi nhận vấn đề và KPI
            baseline.
          </p>
        ) : (
          <div className="integration-table-wrap">
            <table className="integration-table">
              <thead>
                <tr>
                  <th>Vấn đề</th>
                  <th>Nhà máy</th>
                  <th>Trạng thái</th>
                  <th>KPI chính</th>
                  <th>Ngày tạo</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => {
                  const firstMetric = Object.keys(item.baseline)[0];
                  const change = firstMetric
                    ? item.kpi_changes[firstMetric]
                    : undefined;
                  return (
                    <tr key={item.id}>
                      <td>{item.problem}</td>
                      <td>
                        {plants.find((plant) => plant.id === item.plant_id)
                          ?.name ?? `#${item.plant_id}`}
                      </td>
                      <td>
                        <span className="integration-badge ready">
                          {STATUS_LABELS[item.status] ?? item.status}
                        </span>
                      </td>
                      <td>
                        {firstMetric ?? "—"}
                        {change
                          ? ` · ${change.absolute_change > 0 ? "+" : ""}${change.absolute_change}`
                          : ""}
                      </td>
                      <td>
                        {new Intl.DateTimeFormat("vi-VN", {
                          dateStyle: "medium",
                        }).format(new Date(item.created_at))}
                      </td>
                      <td>
                        <button
                          className="integration-button secondary"
                          type="button"
                          onClick={() => {
                            setSelectedId(item.id);
                            setRootCause(item.root_cause ?? "");
                            setAction(item.action ?? "");
                            setOwnerId(
                              item.owner_user_id
                                ? String(item.owner_user_id)
                                : "",
                            );
                            setConclusion(item.conclusion ?? "");
                          }}
                        >
                          Mở hồ sơ
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {!loading && total > 20 && (
          <div className="integration-actions">
            <button
              className="integration-button secondary"
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              Trước
            </button>
            <span className="integration-count">
              Trang {page} / {Math.ceil(total / 20)}
            </span>
            <button
              className="integration-button secondary"
              type="button"
              disabled={page >= Math.ceil(total / 20)}
              onClick={() =>
                setPage((current) => Math.min(Math.ceil(total / 20), current + 1))
              }
            >
              Sau
            </button>
          </div>
        )}
      </section>
    </main>
  );
}
