"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type User = { id: number; display_name: string; role: "admin" | "operator" | "viewer" };
type Organization = { id: number; name: string };
type Factory = { id: number; organization_id: number; name: string };
type Plant = { id: number; name: string; factory_id: number | null; department_id: number | null };
type HistoryItem = {
  id: number;
  analysis_type: string;
  title: string | null;
  source_filename: string | null;
  dataset_id: string | null;
  created_at: string | null;
};
type ReportVersion = {
  id: number;
  version: number;
  content: Record<string, unknown>;
  source_data: Array<Record<string, unknown>>;
  approval_status: string;
  publication_status: string;
  rejection_reason: string | null;
};
type Report = {
  id: number;
  title: string;
  organization_id: number;
  plant_id: number | null;
  current_version: number;
  version: ReportVersion;
  versions?: ReportVersion[];
};

const initialContent = JSON.stringify(
  { title: "Production report", summary: "", kpis: [] },
  null,
  2,
);

export default function ReportsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [selected, setSelected] = useState<Report | null>(null);
  const [title, setTitle] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [plantId, setPlantId] = useState("");
  const [sourceHistoryId, setSourceHistoryId] = useState("");
  const [content, setContent] = useState(initialContent);
  const [reviewReason, setReviewReason] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const request = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await apiFetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload?.detail;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? response.statusText));
    }
    return payload as T;
  }, []);

  const load = useCallback(async () => {
    const [currentUser, organizationData, factoryData, plantData, reportData, historyData] = await Promise.all([
      request<User>("/api/monitoring/auth/me"),
      request<Organization[]>("/api/monitoring/organizations"),
      request<Factory[]>("/api/monitoring/factories"),
      request<Plant[]>("/api/monitoring/plants"),
      request<Report[]>("/api/reports"),
      request<{ items: HistoryItem[] }>("/api/history?page=1&page_size=100"),
    ]);
    setUser(currentUser);
    setOrganizations(organizationData);
    setFactories(factoryData);
    setPlants(plantData);
    setReports(reportData);
    setHistory(historyData.items);
    setSourceHistoryId((existing) => existing || String(historyData.items[0]?.id ?? ""));
    setOrganizationId((existing) => existing || String(organizationData[0]?.id ?? ""));
  }, [request]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load().catch((cause: Error) => setError(cause.message));
      const pending = window.localStorage.getItem("pending-governed-report");
      if (pending) {
        try {
          setContent(JSON.stringify(JSON.parse(pending), null, 2));
          setTitle("Hardware analysis report");
        } catch {
          setError("Không thể đọc nội dung báo cáo đang chờ lưu.");
        }
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function createReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const selectedPlant = plants.find((item) => String(item.id) === plantId);
      if (!selectedPlant?.factory_id) throw new Error("Chọn Plant đã ánh xạ Factory.");
      const source = history.find((item) => String(item.id) === sourceHistoryId);
      if (!source) throw new Error("Chọn một bản ghi Analysis History làm nguồn dữ liệu.");
      const factory = factories.find((item) => item.id === selectedPlant.factory_id);
      if (!factory) throw new Error("Không tìm thấy Factory của Plant.");
      const report = await request<Report>("/api/reports", {
        method: "POST",
        body: JSON.stringify({
          title,
          organization_id: Number(organizationId || factory.organization_id),
          factory_id: factory.id,
          department_id: selectedPlant.department_id,
          plant_id: selectedPlant.id,
          content: JSON.parse(content),
          source_data: [{ analysis_history_id: source.id }],
        }),
      });
      setSelected(report);
      window.localStorage.removeItem("pending-governed-report");
      setMessage("Đã tạo bản nháp. Gửi duyệt để Admin xem xét.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể tạo báo cáo.");
    }
  }

  async function openReport(reportId: number) {
    try {
      const report = await request<Report>(`/api/reports/${reportId}`);
      setSelected(report);
      setTitle(report.title);
      setContent(JSON.stringify(report.version.content, null, 2));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể tải báo cáo.");
    }
  }

  async function act(path: string, body?: unknown) {
    if (!selected) return;
    setError("");
    setMessage("");
    try {
      const updated = await request<Report>(`/api/reports/${selected.id}${path}`, {
        method: "POST",
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      setSelected(updated);
      setMessage("Đã cập nhật workflow báo cáo.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể cập nhật báo cáo.");
    }
  }

  async function saveVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    try {
      const updated = await request<Report>(`/api/reports/${selected.id}/versions`, {
        method: "POST",
        body: JSON.stringify({
          title,
          content: JSON.parse(content),
          source_data: selected.version.source_data.map((item) => ({
            analysis_history_id: Number(item.analysis_history_id),
          })),
        }),
      });
      setSelected(updated);
      setMessage("Đã lưu phiên bản mới dưới dạng bản nháp; bản phát hành cũ không bị thay đổi.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể lưu phiên bản.");
    }
  }

  async function exportReport(format: "pdf" | "xlsx") {
    if (!selected) return;
    try {
      const response = await apiFetch(
        `${API_URL}/api/reports/${selected.id}/export?format=${format}&version=${selected.current_version}`,
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail ?? response.statusText);
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `report-${selected.id}-v${selected.current_version}.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể xuất báo cáo.");
    }
  }

  const canEdit = user?.role === "admin" || user?.role === "operator";
  const isAdmin = user?.role === "admin";
  const selectedStatus = selected?.version.approval_status;

  return (
    <main style={{ maxWidth: 1050, margin: "32px auto", padding: 20, color: "#172033" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div><Link href="/">← Phân tích</Link><h1>Report Governance</h1></div>
        {user && <span>{user.display_name} · {user.role}</span>}
      </header>
      <p>Báo cáo được lưu thành phiên bản bất biến. Chỉ Admin được duyệt và phát hành.</p>
      {error && <p role="alert" style={{ color: "#b42318" }}>{error}</p>}
      {message && <p role="status" style={{ color: "#126b3a" }}>{message}</p>}

      <section style={{ display: "grid", gridTemplateColumns: "minmax(250px, 1fr) 2fr", gap: 20 }}>
        <aside>
          <h2>Báo cáo</h2>
          <ul>{reports.map((report) => (
            <li key={report.id} style={{ marginBottom: 8 }}>
              <button type="button" onClick={() => void openReport(report.id)}>
                {report.title} · v{report.current_version} ({report.version.approval_status}/{report.version.publication_status})
              </button>
            </li>
          ))}</ul>
          {!reports.length && <p>Chưa có báo cáo được phép xem.</p>}
        </aside>

        <section>
          {canEdit && !selected && (
            <form onSubmit={createReport} style={{ display: "grid", gap: 10 }}>
              <h2>Tạo bản nháp</h2>
              <label>Tiêu đề<input required value={title} onChange={(event) => setTitle(event.target.value)} style={{ display: "block", width: "100%", padding: 8 }} /></label>
              <label>Organization<select required value={organizationId} onChange={(event) => setOrganizationId(event.target.value)} style={{ display: "block", width: "100%", padding: 8 }}>
                <option value="">Chọn Organization</option>
                {organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select></label>
              <label>Plant<select required value={plantId} onChange={(event) => {
                const nextPlantId = event.target.value;
                setPlantId(nextPlantId);
                const nextPlant = plants.find((item) => String(item.id) === nextPlantId);
                const nextFactory = factories.find((item) => item.id === nextPlant?.factory_id);
                if (nextFactory) setOrganizationId(String(nextFactory.organization_id));
              }} style={{ display: "block", width: "100%", padding: 8 }}>
                <option value="">Chọn Plant</option>
                {plants.map((item) => {
                  const factory = factories.find((candidate) => candidate.id === item.factory_id);
                  return <option key={item.id} value={item.id}>{item.name}{factory ? ` — ${factory.name}` : ""}</option>;
                })}
              </select></label>
              <label>Nguồn Analysis History<select required value={sourceHistoryId} onChange={(event) => setSourceHistoryId(event.target.value)} style={{ display: "block", width: "100%", padding: 8 }}>
                <option value="">Chọn nguồn dữ liệu</option>
                {history.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title ?? item.analysis_type} · {item.source_filename ?? "không có tên file"}</option>)}
              </select></label>
              <label>Nội dung JSON<textarea required rows={16} value={content} onChange={(event) => setContent(event.target.value)} style={{ display: "block", width: "100%", padding: 8, fontFamily: "monospace" }} /></label>
              <button type="submit">Lưu bản nháp</button>
            </form>
          )}

          {selected && (
            <div style={{ display: "grid", gap: 12 }}>
              <button type="button" onClick={() => setSelected(null)}>← Đóng chi tiết</button>
              <h2>{selected.title} · Phiên bản {selected.current_version}</h2>
              <p>Approval: <strong>{selected.version.approval_status}</strong> · Publication: <strong>{selected.version.publication_status}</strong></p>
              {selected.version.rejection_reason && <p>Lý do từ chối: {selected.version.rejection_reason}</p>}
              {canEdit && <form onSubmit={saveVersion} style={{ display: "grid", gap: 8 }}>
                <label>Tiêu đề phiên bản<input required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
                <label>Nội dung JSON<textarea required rows={14} value={content} onChange={(event) => setContent(event.target.value)} style={{ width: "100%", fontFamily: "monospace" }} /></label>
                <button type="submit">Tạo phiên bản nháp mới</button>
              </form>}
              {canEdit && (selectedStatus === "draft" || selectedStatus === "rejected") && (
                <button type="button" onClick={() => void act("/submit")}>Gửi duyệt</button>
              )}
              {isAdmin && selectedStatus === "pending_approval" && <>
                <label>Lý do từ chối (bắt buộc nếu từ chối)<input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></label>
                <button type="button" onClick={() => void act("/review", { decision: "approved" })}>Duyệt</button>
                <button type="button" onClick={() => void act("/review", { decision: "rejected", reason: reviewReason })}>Từ chối</button>
              </>}
              {isAdmin && selectedStatus === "approved" && <button type="button" onClick={() => void act("/publish")}>Phát hành và khóa phiên bản</button>}
              {selected.version.publication_status === "published" && <>
                <button type="button" onClick={() => void exportReport("pdf")}>Tải PDF</button>
                <button type="button" onClick={() => void exportReport("xlsx")}>Tải Excel</button>
              </>}
              {selected.versions && <div><h3>Lịch sử phiên bản</h3><ul>{selected.versions.map((version) => (
                <li key={version.id}>v{version.version}: {version.approval_status} / {version.publication_status}</li>
              ))}</ul></div>}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
