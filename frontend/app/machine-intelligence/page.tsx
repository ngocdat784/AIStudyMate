"use client";

import { useState } from "react";

import FileDropzone from "../../components/file-dropzone";
import { apiFetch } from "../../lib/api";

type MachineMetrics = {
  entity: string;
  availability_percent: number | null;
  performance_percent: number | null;
  quality_percent: number | null;
  oee_percent: number | null;
  machine_utilization_percent: number | null;
  downtime_minutes: number | null;
  failure_count: number;
  performance_record_count: number;
  performance_validation: string;
  downtime_event_count: number;
  maintenance_event_count: number;
  mtbf_hours: number | null;
  mttr_minutes: number | null;
};

type MachineAnalysis = {
  machine_count: number;
  overall: Omit<MachineMetrics, "entity"> & { production: number | null };
  by_machine: MachineMetrics[];
  by_line: MachineMetrics[];
  by_shift: MachineMetrics[];
  downtime: {
    planned_downtime_minutes: number;
    unplanned_downtime_minutes: number;
    unclassified_downtime_minutes: number;
    total_downtime_minutes: number;
    event_count: number;
    pareto: Array<{
      reason: string;
      duration_minutes: number;
      event_count: number;
      share_percent: number;
      cumulative_share_percent: number;
    }>;
    normalized_events: Array<{
      machine: string;
      timestamp: string | null;
      event_type: string;
      status_class: string;
      duration_minutes: number | null;
      reason: string | null;
      downtime_classification: string;
      source_row: number;
    }>;
  };
  anomalies: Array<Record<string, string | number>>;
  maintenance_priorities: Array<{
    machine: string;
    risk: string;
    failure_count: number;
    unplanned_downtime_minutes: number;
    downtime_event_count: number;
    maintenance_event_count: number;
    maintenance_passed_count: number;
    maintenance_failed_count: number;
    maintenance_monitoring_count: number;
    maintenance_unknown_count: number;
    maintenance_success_rate_percent: number | null;
    recommended_priority: string;
    recommendation: string;
    maintenance_history: Array<{
      action: string | null;
      outcome: string | null;
      outcome_status: string;
      timestamp: string | null;
      source_row: number;
    }>;
  }>;
  maintenance_outcomes: Array<{
    machine: string | null;
    action: string | null;
    outcome: string | null;
    outcome_status: string;
    timestamp: string | null;
    source_row: number;
  }>;
  analysis_limitations: string[];
};

function number(value: number | null | undefined, digits = 1): string {
  return value == null
    ? "Chưa đủ dữ liệu"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: digits });
}

function normalizeMaintenanceStatus(outcome: string | null | undefined): string {
  const normalized = (outcome ?? "")
    .trim()
    .toLocaleLowerCase("vi")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
  if (!normalized || ["none", "n/a", "na", "null", "unknown", "unclassified", "not specified"].includes(normalized)) {
    return "unknown";
  }

  const failureMarkers = [
    "failed", "failure", "unsuccessful", "not passed", "not successful",
    "rejected", "aborted", "incomplete", "not complete", "not completed",
    "broken", "unrepaired", "thất bại", "that bai", "không đạt", "khong dat",
    "không hoàn thành", "khong hoan thanh", "chưa hoàn thành",
    "chua hoan thanh", "không thành công", "khong thanh cong",
    "từ chối", "tu choi",
  ];
  if (failureMarkers.some((marker) => normalized.includes(marker))) {
    return "failed";
  }

  const monitoringMarkers = [
    "monitoring required", "monitor", "observation", "observe", "follow up",
    "follow-up", "recheck", "pending", "in progress", "waiting", "conditional",
    "cần giám sát", "can giam sat", "cần theo dõi", "can theo doi",
    "theo dõi", "theo doi", "đang giám sát", "dang giam sat",
    "đang theo dõi", "dang theo doi", "đang thực hiện", "dang thuc hien",
    "cần kiểm tra lại", "can kiem tra lai", "chờ", "cho",
  ];
  if (monitoringMarkers.some((marker) => normalized.includes(marker))) {
    return "monitoring";
  }

  const successMarkers = [
    "completed", "complete", "passed", "pass", "success", "successful",
    "succeeded", "fixed", "resolved", "restored", "repaired", "verified",
    "validated", "approved", "good", "ok", "done", "hoan thanh",
    "hoàn thành", "thanh cong", "thành công", "đạt", "dat", "khoi phuc",
    "khôi phục", "sua chua xong", "sửa chữa xong",
  ];
  if (successMarkers.some((marker) => normalized.includes(marker))) {
    return "successful";
  }
  return "unknown";
}

function maintenanceStatusLabel(status: string): string {
  if (status === "successful") return "Thành công";
  if (status === "failed") return "Thất bại";
  if (status === "monitoring") return "Cần giám sát";
  return "Chưa phân loại";
}

function MachineTable({
  title,
  rows,
}: {
  title: string;
  rows: MachineMetrics[];
}) {
  return (
    <section className="executive-card">
      <div className="executive-card-heading">
        <h2>{title}</h2>
      </div>
      {rows.length === 0 ? (
        <p className="executive-empty">Không có dữ liệu phân nhóm.</p>
      ) : (
        <div className="production-table-scroll">
          <table className="production-table">
            <thead>
              <tr>
                <th>Thiết bị / nhóm</th>
                <th>Availability</th>
                <th>Performance</th>
                <th>Quality</th>
                <th>OEE</th>
                <th>Downtime</th>
                <th>Failures</th>
                <th>MTBF / MTTR</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.entity}>
                  <th scope="row">{row.entity}</th>
                  <td>{number(row.availability_percent)}%</td>
                  <td>{number(row.performance_percent)}%</td>
                  <td>{number(row.quality_percent)}%</td>
                  <td>{number(row.oee_percent)}%</td>
                  <td>{number(row.downtime_minutes)} phút</td>
                  <td>{row.failure_count}</td>
                  <td>
                    {number(row.mtbf_hours)} giờ / {number(row.mttr_minutes)} phút
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function MachineIntelligencePage() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceTimezone, setSourceTimezone] = useState("UTC");
  const [downtimeUnit, setDowntimeUnit] = useState("minutes");
  const [durationUnit, setDurationUnit] = useState("minutes");
  const [cycleTimeUnit, setCycleTimeUnit] = useState("seconds");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MachineAnalysis | null>(null);

  async function submitAnalysis(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("source_timezone", sourceTimezone);
    formData.append("downtime_unit", downtimeUnit);
    formData.append("duration_unit", durationUnit);
    formData.append("cycle_time_unit", cycleTimeUnit);
    try {
      const response = await apiFetch("/api/machine-intelligence/analyze", {
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
      setResult(body as MachineAnalysis);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Không thể phân tích dữ liệu thiết bị.",
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
            <span className="executive-eyebrow">HARDWAREAI · PHASE 2.4</span>
            <h1>Machine Intelligence</h1>
            <p>
              Đánh giá OEE, thời gian dừng, độ tin cậy, bất thường và ưu tiên
              bảo trì từ dữ liệu thiết bị.
            </p>
          </div>
        </header>

        <form className="executive-card production-upload" onSubmit={submitAnalysis}>
          <FileDropzone
            accept=".csv,.xlsx,.xlsm,.xls"
            title="File máy móc"
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
          <div className="production-unit-fields">
            <label>
              Downtime unit
              <select value={downtimeUnit} onChange={(event) => setDowntimeUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Available / operating time unit
              <select value={durationUnit} onChange={(event) => setDurationUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
            <label>
              Cycle time unit
              <select value={cycleTimeUnit} onChange={(event) => setCycleTimeUnit(event.target.value)}>
                <option value="seconds">Giây</option>
                <option value="minutes">Phút</option>
                <option value="hours">Giờ</option>
              </select>
            </label>
          </div>
          <button type="submit" disabled={loading || !file}>
            {loading ? "Đang phân tích…" : "Phân tích thiết bị"}
          </button>
          {error && <p className="executive-error" role="alert">{error}</p>}
        </form>

        {loading && (
          <p className="executive-loading" role="status">
            Đang chuẩn hóa machine events và tính KPI thiết bị…
          </p>
        )}

        {result && (
          <>
            <section className="executive-card">
              <span className="executive-eyebrow">MACHINE OVERVIEW</span>
              <h2>{result.machine_count} thiết bị được nhận diện</h2>
              <p>
                Sản lượng {number(result.overall.production, 0)} · Downtime{" "}
                {number(result.downtime.total_downtime_minutes)} phút ·{" "}
                {result.downtime.event_count} sự kiện dừng.
              </p>
            </section>

            <section className="executive-kpi-grid" aria-label="OEE KPIs">
              {[
                ["Availability", result.overall.availability_percent],
                ["Performance", result.overall.performance_percent],
                ["Quality", result.overall.quality_percent],
                ["OEE", result.overall.oee_percent],
                ["Machine utilization", result.overall.machine_utilization_percent],
                ["MTBF", result.overall.mtbf_hours],
                ["MTTR", result.overall.mttr_minutes],
                ["Unplanned downtime", result.downtime.unplanned_downtime_minutes],
              ].map(([label, value]) => (
                <article className="executive-kpi-card" key={String(label)}>
                  <span>{label}</span>
                  <strong>
                    {number(value as number | null)}
                    {label === "MTBF"
                      ? " giờ"
                      : label === "MTTR" || label === "Unplanned downtime"
                        ? " phút"
                        : "%"}
                  </strong>
                </article>
              ))}
            </section>

            <MachineTable title="OEE và độ tin cậy theo máy" rows={result.by_machine} />
            <div className="executive-two-column">
              <MachineTable title="OEE theo dây chuyền" rows={result.by_line} />
              <MachineTable title="OEE theo ca" rows={result.by_shift} />
            </div>

            <section className="executive-card">
              <h2>Downtime Pareto</h2>
              <p>
                Planned {number(result.downtime.planned_downtime_minutes)} phút ·
                Unplanned {number(result.downtime.unplanned_downtime_minutes)} phút ·
                Unclassified {number(result.downtime.unclassified_downtime_minutes)} phút
              </p>
              {result.downtime.pareto.length === 0 ? (
                <p className="executive-empty">Chưa có sự kiện downtime.</p>
              ) : (
                <div className="production-period-list">
                  {result.downtime.pareto.map((item) => (
                    <div className="production-period-row" key={item.reason}>
                      <strong>{item.reason}</strong>
                      <div className="production-period-bars">
                        <span
                          className="production-bar production-bar-actual"
                          style={{ width: `${Math.max(2, item.share_percent)}%` }}
                        />
                      </div>
                      <span>
                        {number(item.duration_minutes)} phút · {item.event_count} lần ·
                        {" "}{number(item.cumulative_share_percent)}% tích lũy
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <div className="executive-two-column">
              <section className="executive-card">
                <h2>Anomaly screening</h2>
                {result.anomalies.length === 0 ? (
                  <p className="executive-empty">Chưa phát hiện tín hiệu bất thường.</p>
                ) : (
                  <ul>
                    {result.anomalies.map((item, index) => (
                      <li key={`${String(item.machine)}-${String(item.type)}-${index}`}>
                        <strong>{String(item.machine)}</strong>: {String(item.type)}
                        {" · "}
                        {Object.entries(item)
                          .filter(([key]) => !["machine", "type"].includes(key))
                          .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
                          .join(" · ")}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <section className="executive-card">
                <h2>Ưu tiên bảo trì</h2>
                {result.maintenance_priorities.length === 0 ? (
                  <p className="executive-empty">Chưa có thiết bị cần ưu tiên theo ngưỡng sàng lọc.</p>
                ) : (
                  <ul>
                    {result.maintenance_priorities.map((item) => (
                      <li key={item.machine}>
                        <strong>{item.machine} · {item.risk}</strong>: {item.recommendation}
                        {" "}({item.failure_count} hỏng xác nhận, {item.downtime_event_count} sự kiện dừng,
                        {" "}{number(item.unplanned_downtime_minutes)} phút dừng ngoài kế hoạch,
                        {" "}{item.maintenance_event_count} lần bảo trì,
                        {" "}{item.maintenance_passed_count} thành công,
                        {" "}{item.maintenance_failed_count} thất bại,
                        {" "}{item.maintenance_monitoring_count} cần giám sát,
                        {" "}{number(item.maintenance_success_rate_percent)}% tỷ lệ thành công)
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            <section className="executive-card">
              <h2>Theo dõi kết quả bảo trì</h2>
              {result.maintenance_outcomes.length === 0 ? (
                <p className="executive-empty">
                  Nạp cột Maintenance Action và Maintenance Outcome để theo dõi kết quả đã ghi nhận.
                </p>
              ) : (
                <ul>
                  {result.maintenance_outcomes.map((item) => (
                    <li key={`${item.machine}-${item.source_row}`}>
                      {item.machine ?? "Chưa gán máy"} · {item.timestamp ?? "Chưa có thời gian"} ·
                      {" "}{item.action ?? "Chưa ghi hành động"} → {item.outcome ?? "Chưa ghi kết quả"}
                      {" "}({maintenanceStatusLabel(
                        item.outcome_status ||
                        normalizeMaintenanceStatus(item.outcome)
                      )})
                    </li>
                  ))}
                </ul>
              )}
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
