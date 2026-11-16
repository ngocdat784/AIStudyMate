"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../lib/api";
import { hasAuthToken, redirectToLogin } from "../../lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

type Plant = {
  id: number;
  name: string;
  lines: Array<{ id: number; name: string }>;
};

type Connector = {
  id: string;
  name: string;
  plant_id: number;
  line_id: number | null;
  column_mapping: Record<string, string>;
  normalization: {
    date_timezone: string;
    downtime_unit: "minutes" | "seconds" | "hours";
    uppercase_code_fields: string[];
  };
  enabled: boolean;
  last_sync_at: string | null;
};

type SyncRun = {
  id: string;
  connector_id: string;
  status: "running" | "succeeded" | "failed" | "duplicate";
  source_filename: string;
  source_file_sha256: string;
  rows_read: number;
  rows_imported: number;
  rows_rejected: number;
  validation_report: {
    issues?: Array<{ row: number; field: string; code: string }>;
    normalization?: Record<string, string | string[]>;
  } | null;
  lineage: Record<string, unknown>;
  dataset_id: number | null;
  error_message: string | null;
  created_at: string;
};

type Registry = {
  connectors: Array<{
    type: string;
    name: string;
    status: string;
    formats: string[];
    description: string;
  }>;
  not_connected: string[];
  notice: string;
  canonical_fields: string[];
  required_fields: string[];
  max_file_size_bytes: number;
  max_rows: number;
};

type Preview = {
  filename: string;
  file_sha256: string;
  file_size_bytes: number;
  row_count: number;
  columns: string[];
  suggested_mapping: Record<string, string>;
  sample_rows: Array<Record<string, unknown>>;
};

type ValidationIssue = {
  row: number;
  field: string;
  code: string;
};

class IntegrationRequestError extends Error {
  constructor(
    message: string,
    readonly detail: unknown,
  ) {
    super(message);
  }
}

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string | null) {
  if (!value) return "Chưa đồng bộ";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API_URL}${path}`, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail?.message === "string"
          ? detail.message
          : `Yêu cầu thất bại (HTTP ${response.status}).`;
    throw new IntegrationRequestError(message, detail);
  }
  return payload as T;
}

export default function IntegrationsPage() {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [syncRuns, setSyncRuns] = useState<SyncRun[]>([]);
  const [selectedConnectorId, setSelectedConnectorId] = useState("");
  const [connectorName, setConnectorName] = useState("");
  const [plantId, setPlantId] = useState("");
  const [lineId, setLineId] = useState("");
  const [dateTimezone, setDateTimezone] = useState("UTC");
  const [downtimeUnit, setDowntimeUnit] = useState<
    "minutes" | "seconds" | "hours"
  >("minutes");
  const [uppercaseCodes, setUppercaseCodes] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [selectedRunId, setSelectedRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [validationIssues, setValidationIssues] = useState<ValidationIssue[]>(
    [],
  );

  const selectedPlant = useMemo(
    () => plants.find((plant) => String(plant.id) === plantId) ?? null,
    [plants, plantId],
  );
  const selectedConnector = useMemo(
    () =>
      connectors.find((connector) => connector.id === selectedConnectorId) ??
      null,
    [connectors, selectedConnectorId],
  );
  const selectedRun = syncRuns.find((run) => run.id === selectedRunId) ?? null;

  const refresh = useCallback(async () => {
    const [registryData, plantData, connectorData, historyData] =
      await Promise.all([
        requestJson<Registry>("/api/integrations/registry"),
        requestJson<Plant[]>("/api/monitoring/plants"),
        requestJson<Connector[]>("/api/integrations/connectors"),
        requestJson<{ items: SyncRun[] }>("/api/integrations/sync-runs?page_size=20"),
      ]);
    setRegistry(registryData);
    setPlants(plantData);
    setConnectors(connectorData);
    setSyncRuns(historyData.items);
  }, []);

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
                : "Không thể tải cấu hình tích hợp.",
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

  function chooseConnector(id: string) {
    setSelectedConnectorId(id);
    setError("");
    setMessage("");
    const connector = connectors.find((item) => item.id === id);
    if (!connector) {
      setConnectorName("");
      setPlantId("");
      setLineId("");
      setMapping({});
      setDateTimezone("UTC");
      setDowntimeUnit("minutes");
      setUppercaseCodes(false);
      return;
    }
    setConnectorName(connector.name);
    setPlantId(String(connector.plant_id));
    setLineId(connector.line_id ? String(connector.line_id) : "");
    setMapping(connector.column_mapping);
    setDateTimezone(connector.normalization.date_timezone ?? "UTC");
    setDowntimeUnit(connector.normalization.downtime_unit ?? "minutes");
    setUppercaseCodes(
      connector.normalization.uppercase_code_fields.length > 0,
    );
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    setPreview(null);
    setMapping({});
    setError("");
    setMessage("");
    if (!chosen) return;

    setBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", chosen);
      const result = await requestJson<Preview>("/api/integrations/preview", {
        method: "POST",
        body: formData,
      });
      setPreview(result);
      setMapping(result.suggested_mapping);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Không thể xem trước file.",
      );
    } finally {
      setBusy(false);
    }
  }

  function mappedColumns() {
    return Object.fromEntries(
      Object.entries(mapping).filter(([, target]) => Boolean(target)),
    );
  }

  function connectorPayload() {
    return {
      name: connectorName.trim(),
      plant_id: Number(plantId),
      line_id: lineId ? Number(lineId) : null,
      column_mapping: mappedColumns(),
      normalization: {
        date_timezone: dateTimezone.trim(),
        downtime_unit: downtimeUnit,
        uppercase_code_fields: uppercaseCodes
          ? ["Product", "Model", "Component", "Supplier", "Batch", "Machine"]
          : [],
      },
    };
  }

  async function saveConnector() {
    setError("");
    setMessage("");
    if (!connectorName.trim() || !plantId) {
      setError("Hãy nhập tên connector và chọn nhà máy.");
      return null;
    }

    setBusy(true);
    try {
      const payload = connectorPayload();
      const saved = selectedConnectorId
        ? await requestJson<Connector>(
            `/api/integrations/connectors/${selectedConnectorId}`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            },
          )
        : await requestJson<Connector>("/api/integrations/connectors", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, connector_type: "file" }),
          });
      setSelectedConnectorId(saved.id);
      await refresh();
      setMessage("Đã lưu cấu hình connector.");
      return saved;
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Không thể lưu cấu hình connector.",
      );
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function runSync(connectorId: string) {
    if (!file) {
      setError("Chọn CSV hoặc XLSX trước khi đồng bộ.");
      return;
    }
    setError("");
    setMessage("");
    setValidationIssues([]);
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const run = await requestJson<SyncRun & { idempotent_replay: boolean }>(
        `/api/integrations/connectors/${connectorId}/sync`,
        { method: "POST", body: formData },
      );
      setMessage(
        run.idempotent_replay
          ? "File này đã được đồng bộ trước đó; dữ liệu không bị nhập trùng."
          : `Đã nhập ${run.rows_imported.toLocaleString("vi-VN")} dòng vào dataset #${run.dataset_id}.`,
      );
      setSelectedRunId(run.id);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Đồng bộ file thất bại.",
      );
      if (cause instanceof IntegrationRequestError) {
        const detail = cause.detail;
        const report =
          detail && typeof detail === "object" && "validation_report" in detail
            ? detail.validation_report
            : null;
        if (
          report &&
          typeof report === "object" &&
          "issues" in report &&
          Array.isArray(report.issues)
        ) {
          setValidationIssues(report.issues as ValidationIssue[]);
        }
      }
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  async function saveAndSync() {
    if (!file || !preview) {
      setError("Chọn và xem trước CSV/XLSX trước khi lưu connector.");
      return;
    }
    const connector = await saveConnector();
    if (connector) await runSync(connector.id);
  }

  async function toggleConnector(connector: Connector) {
    setBusy(true);
    setError("");
    try {
      await requestJson<Connector>(
        `/api/integrations/connectors/${connector.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !connector.enabled }),
        },
      );
      await refresh();
      setMessage(
        connector.enabled
          ? "Connector đã được tạm dừng."
          : "Connector đã được bật lại.",
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Không thể đổi trạng thái.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <main className="integration-page">
        <p role="status">Đang tải cấu hình tích hợp…</p>
      </main>
    );
  }

  return (
    <main className="integration-page">
      <section className="integration-hero">
        <div>
          <span className="integration-eyebrow">PHASE 13E · DATA FOUNDATION</span>
          <h1>Tích hợp dữ liệu sản xuất</h1>
          <p>
            Đăng ký nguồn dữ liệu, ánh xạ sang schema HardwareAI, kiểm tra và
            chuẩn hóa trước khi đưa vào giám sát.
          </p>
        </div>
        <div className="integration-hero-status">
          <span className="integration-status-dot" />
          CSV / XLSX connector sẵn sàng
        </div>
      </section>

      {error && (
        <div className="integration-notice integration-notice-error" role="alert">
          <strong>Không thể hoàn tất thao tác</strong>
          <span>{error}</span>
          {validationIssues.length ? (
            <ul>
              {validationIssues.map((issue, index) => (
                <li key={`${issue.row}-${issue.field}-${index}`}>
                  Dòng {issue.row} · {issue.field} · {issue.code}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
      {message && (
        <div className="integration-notice integration-notice-success" role="status">
          {message}
        </div>
      )}

      <section className="integration-registry">
        <div className="integration-section-heading">
          <div>
            <span className="integration-eyebrow">CONNECTOR REGISTRY</span>
            <h2>Nguồn dữ liệu đã triển khai</h2>
          </div>
          <span className="integration-count">
            {registry?.connectors.length ?? 0} connector khả dụng
          </span>
        </div>
        <div className="integration-source-grid">
          {registry?.connectors.map((connector) => (
            <article className="integration-source-card" key={connector.type}>
              <span className="integration-source-icon" aria-hidden="true">
                ⇧
              </span>
              <div>
                <h3>{connector.name}</h3>
                <p>{connector.description}</p>
              </div>
              <span className="integration-available">Đang hoạt động</span>
            </article>
          ))}
          <article className="integration-source-card integration-source-planned">
            <span className="integration-source-icon" aria-hidden="true">
              +
            </span>
            <div>
              <h3>ERP · MES · SCADA · PLC</h3>
              <p>{registry?.notice}</p>
            </div>
            <span className="integration-planned">Chưa kết nối</span>
          </article>
        </div>
      </section>

      <section className="integration-workspace">
        <div className="integration-section-heading">
          <div>
            <span className="integration-eyebrow">FILE CONNECTOR</span>
            <h2>Thiết lập ánh xạ và đồng bộ</h2>
          </div>
          <span className="integration-limit">
            Tối đa {formatBytes(registry?.max_file_size_bytes ?? 10 * 1024 * 1024)}{" "}
            · {(registry?.max_rows ?? 100000).toLocaleString("vi-VN")} dòng
          </span>
        </div>

        <div className="integration-setup-grid">
          <div className="integration-panel">
            <label>
              Dùng connector đã lưu
              <select
                value={selectedConnectorId}
                onChange={(event) => chooseConnector(event.target.value)}
              >
                <option value="">Tạo connector mới</option>
                {connectors.map((connector) => (
                  <option key={connector.id} value={connector.id}>
                    {connector.name} · {connector.enabled ? "bật" : "tạm dừng"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Tên nguồn dữ liệu
              <input
                value={connectorName}
                onChange={(event) => setConnectorName(event.target.value)}
                maxLength={160}
                placeholder="Ví dụ: Báo cáo sản xuất ca ngày"
              />
            </label>
            <div className="integration-form-row">
              <label>
                Nhà máy
                <select
                  value={plantId}
                  onChange={(event) => {
                    setPlantId(event.target.value);
                    setLineId("");
                  }}
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
                Dây chuyền
                <select
                  value={lineId}
                  onChange={(event) => setLineId(event.target.value)}
                  disabled={!selectedPlant}
                >
                  <option value="">Toàn nhà máy</option>
                  {selectedPlant?.lines.map((line) => (
                    <option key={line.id} value={line.id}>
                      {line.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="integration-form-row">
              <label>
                Múi giờ trong file
                <input
                  value={dateTimezone}
                  onChange={(event) => setDateTimezone(event.target.value)}
                  placeholder="Asia/Ho_Chi_Minh"
                />
              </label>
              <label>
                Đơn vị downtime nguồn
                <select
                  value={downtimeUnit}
                  onChange={(event) =>
                    setDowntimeUnit(
                      event.target.value as "minutes" | "seconds" | "hours",
                    )
                  }
                >
                  <option value="minutes">Phút</option>
                  <option value="seconds">Giây</option>
                  <option value="hours">Giờ</option>
                </select>
              </label>
            </div>
            <label className="integration-checkbox">
              <input
                type="checkbox"
                checked={uppercaseCodes}
                onChange={(event) => setUppercaseCodes(event.target.checked)}
              />
              Chuẩn hóa mã sản phẩm, model, component, supplier, batch và máy
              thành chữ hoa
            </label>
            <label>
              Chọn file nguồn
              <input
                type="file"
                accept=".csv,.xlsx"
                onChange={(event) => void handleFileChange(event)}
              />
            </label>
            {file && (
              <div className="integration-file-meta">
                <strong>{file.name}</strong>
                <span>
                  {formatBytes(file.size)}
                  {preview
                    ? ` · ${preview.row_count.toLocaleString("vi-VN")} dòng`
                    : ""}
                </span>
              </div>
            )}
            {selectedConnector && !selectedConnector.enabled && (
              <p className="integration-inline-warning">
                Connector này đang tạm dừng; cần bật lại trước khi đồng bộ.
              </p>
            )}
            <div className="integration-actions">
              <button
                type="button"
                className="integration-button secondary"
                disabled={busy || !connectorName.trim() || !plantId}
                onClick={() => void saveConnector()}
              >
                Lưu cấu hình
              </button>
              <button
                type="button"
                className="integration-button primary"
                disabled={
                  busy ||
                  !file ||
                  !preview ||
                  !connectorName.trim() ||
                  !plantId ||
                  Boolean(selectedConnector && !selectedConnector.enabled)
                }
                onClick={() => void saveAndSync()}
              >
                {busy ? "Đang xử lý…" : "Lưu và đồng bộ"}
              </button>
            </div>
          </div>

          <div className="integration-panel integration-mapping-panel">
            <div className="integration-panel-heading">
              <div>
                <h3>Data mapping</h3>
                <p>
                  Chọn trường HardwareAI cho từng cột. Các trường bổ sung có
                  thể lưu dưới dạng thuộc tính mở rộng.
                </p>
              </div>
              <span>
                Bắt buộc: {registry?.required_fields.join(", ") ?? "Date, Production"}
              </span>
            </div>
            {!preview ? (
              <div className="integration-empty">
                <strong>Chưa có file xem trước</strong>
                <span>
                  Chọn CSV/XLSX để nhận diện cột và gợi ý ánh xạ dữ liệu.
                </span>
              </div>
            ) : (
              <>
                <div className="integration-mapping-list">
                  {preview.columns.map((column) => {
                    const suggested =
                      preview.suggested_mapping[column] ??
                      `attributes.${column
                        .trim()
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, "_")
                        .replace(/^_|_$/g, "")
                        .slice(0, 60) || "source_field"}`;
                    const current = mapping[column] ?? "";
                    return (
                      <label className="integration-map-row" key={column}>
                        <span title={column}>{column}</span>
                        <span aria-hidden="true">→</span>
                        <select
                          value={current}
                          onChange={(event) =>
                            setMapping((previous) => ({
                              ...previous,
                              [column]: event.target.value,
                            }))
                          }
                        >
                          <option value="">Bỏ qua cột</option>
                          {registry?.canonical_fields.map((field) => (
                            <option key={field} value={field}>
                              {field}
                            </option>
                          ))}
                          <option value={suggested}>
                            Lưu thêm: {suggested}
                          </option>
                          {current.startsWith("attributes.") &&
                            current !== suggested && (
                              <option value={current}>{current}</option>
                            )}
                        </select>
                      </label>
                    );
                  })}
                </div>
                <div className="integration-preview-table-wrap">
                  <table className="integration-preview-table">
                    <thead>
                      <tr>
                        {preview.columns.map((column) => (
                          <th key={column}>{column}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample_rows.map((row, index) => (
                        <tr key={index}>
                          {preview.columns.map((column) => (
                            <td key={column}>
                              {String(row[column] ?? "—")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      </section>

      <section className="integration-panel integration-connectors-section">
        <div className="integration-section-heading">
          <div>
            <span className="integration-eyebrow">SAVED CONNECTORS</span>
            <h2>Nguồn dữ liệu đã đăng ký</h2>
          </div>
        </div>
        {connectors.length === 0 ? (
          <div className="integration-empty">
            <strong>Chưa có connector</strong>
            <span>
              Tạo connector file đầu tiên để lưu ánh xạ và lịch sử đồng bộ.
            </span>
          </div>
        ) : (
          <div className="integration-table-wrap">
            <table className="integration-table">
              <thead>
                <tr>
                  <th>Tên nguồn</th>
                  <th>Nhà máy</th>
                  <th>Ánh xạ</th>
                  <th>Đồng bộ gần nhất</th>
                  <th>Trạng thái</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {connectors.map((connector) => (
                  <tr key={connector.id}>
                    <td>{connector.name}</td>
                    <td>
                      {plants.find((plant) => plant.id === connector.plant_id)
                        ?.name ?? `#${connector.plant_id}`}
                    </td>
                    <td>
                      {Object.keys(connector.column_mapping).length} cột
                    </td>
                    <td>{formatDate(connector.last_sync_at)}</td>
                    <td>
                      <span
                        className={
                          connector.enabled
                            ? "integration-badge ready"
                            : "integration-badge paused"
                        }
                      >
                        {connector.enabled ? "Đang bật" : "Tạm dừng"}
                      </span>
                    </td>
                    <td>
                      <div className="integration-row-actions">
                        <button
                          type="button"
                          className="integration-button secondary"
                          onClick={() => chooseConnector(connector.id)}
                        >
                          Cấu hình
                        </button>
                        <button
                          type="button"
                          className="integration-button secondary"
                          disabled={busy}
                          onClick={() => void toggleConnector(connector)}
                        >
                          {connector.enabled ? "Tạm dừng" : "Bật lại"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="integration-panel integration-history-section">
        <div className="integration-section-heading">
          <div>
            <span className="integration-eyebrow">SYNC HISTORY & LINEAGE</span>
            <h2>Lịch sử đồng bộ</h2>
          </div>
          <button
            type="button"
            className="integration-button secondary"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void refresh()
                .catch((cause: unknown) =>
                  setError(
                    cause instanceof Error
                      ? cause.message
                      : "Không thể làm mới lịch sử.",
                  ),
                )
                .finally(() => setBusy(false));
            }}
          >
            Làm mới
          </button>
        </div>
        {syncRuns.length === 0 ? (
          <div className="integration-empty">
            <strong>Chưa có lần đồng bộ nào</strong>
            <span>Kết quả hợp lệ và lỗi kiểm tra sẽ xuất hiện tại đây.</span>
          </div>
        ) : (
          <div className="integration-table-wrap">
            <table className="integration-table">
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Nguồn file</th>
                  <th>Trạng thái</th>
                  <th>Đọc / nhập / lỗi</th>
                  <th>Dataset</th>
                  <th>Lineage</th>
                </tr>
              </thead>
              <tbody>
                {syncRuns.map((run) => (
                  <tr key={run.id}>
                    <td>{formatDate(run.created_at)}</td>
                    <td>{run.source_filename}</td>
                    <td>
                      <span
                        className={`integration-badge ${run.status}`}
                      >
                        {run.status === "succeeded"
                          ? "Hoàn tất"
                          : run.status === "failed"
                            ? "Thất bại"
                            : run.status === "duplicate"
                              ? "Trùng lặp"
                              : "Đang chạy"}
                      </span>
                      {run.error_message && (
                        <small className="integration-error-text">
                          {run.error_message}
                        </small>
                      )}
                    </td>
                    <td>
                      {run.rows_read} / {run.rows_imported} / {run.rows_rejected}
                    </td>
                    <td>{run.dataset_id ? `#${run.dataset_id}` : "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="integration-text-button"
                        onClick={() => setSelectedRunId(run.id)}
                      >
                        Xem nguồn &amp; mapping
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {selectedRun && (
          <details className="integration-lineage-detail" open>
            <summary>
              Lineage · {selectedRun.source_filename} ·{" "}
              {selectedRun.source_file_sha256.slice(0, 16)}…
            </summary>
            <dl>
              <dt>Sync ID</dt>
              <dd>{selectedRun.id}</dd>
              <dt>SHA-256 nguồn</dt>
              <dd>{selectedRun.source_file_sha256}</dd>
              <dt>Connector</dt>
              <dd>{selectedRun.connector_id}</dd>
              <dt>Mapping bất biến khi nhập</dt>
              <dd>
                <pre>{JSON.stringify(selectedRun.lineage.mapping, null, 2)}</pre>
              </dd>
              <dt>Chuẩn hóa</dt>
              <dd>
                <pre>
                  {JSON.stringify(selectedRun.lineage.normalization, null, 2)}
                </pre>
              </dd>
              {selectedRun.validation_report?.issues?.length ? (
                <>
                  <dt>Dòng cần sửa</dt>
                  <dd>
                    {selectedRun.validation_report.issues.map((issue, index) => (
                      <span key={`${issue.row}-${index}`}>
                        Dòng {issue.row}: {issue.field} — {issue.code}
                        {index < selectedRun.validation_report!.issues!.length - 1
                          ? "; "
                          : ""}
                      </span>
                    ))}
                  </dd>
                </>
              ) : null}
            </dl>
          </details>
        )}
      </section>
    </main>
  );
}
