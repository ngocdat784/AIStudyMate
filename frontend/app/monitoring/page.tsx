"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AUTH_CHANGED_EVENT, notifyAuthChanged } from "../../lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type User = {
  id: number;
  email?: string;
  display_name: string;
  role: "admin" | "operator" | "viewer";
  email_notifications?: boolean;
  is_active?: boolean;
  account_status?: string;
};

type UserDirectoryItem = Pick<User, "id" | "display_name" | "role" | "is_active" | "account_status">;
type AuthSession = {
  id: number;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
  user_agent: string | null;
};
type AlertHistoryItem = {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  actor_name: string;
  note: string | null;
  created_at: string;
};

type Plant = {
  id: number;
  name: string;
  timezone: string;
  factory_id?: number | null;
  department_id?: number | null;
  lines: { id: number; name: string }[];
};
type Organization = { id: number; name: string };
type Factory = { id: number; organization_id: number; name: string };
type Department = { id: number; factory_id: number; name: string };
type OrganizationScope = {
  factory_ids: number[];
  department_ids: number[];
  plant_ids: number[];
};

type AlertItem = {
  id: number;
  alert_type: "threshold" | "statistical";
  plant_id: number;
  plant_name: string | null;
  line_name: string | null;
  metric: string;
  scope_type: string;
  scope_value: string | null;
  severity: string;
  title: string;
  summary: string;
  ai_explanation: string | null;
  observed_value: number;
  threshold_value: number | null;
  impact: { production?: number; defects?: number };
  evidence: Array<Record<string, unknown>>;
  recommended_check: string;
  confidence: string;
  status: "open" | "acknowledged" | "investigating" | "snoozed" | "resolved" | "closed";
  snoozed_until: string | null;
  feedback: "useful" | "false_positive" | "duplicate" | null;
  assigned_to: number | null;
  assigned_name: string | null;
  detected_at: string;
};

type Dataset = {
  id: number;
  name: string;
  plant_id: number;
  line_id: number | null;
  granularity: string;
  schedule_interval: number;
  schedule_unit: string;
  enabled: boolean;
  last_checked_at: string | null;
  created_at: string;
  row_count: number;
};

type Rule = {
  id: number;
  name: string;
  plant_id: number;
  line_id: number | null;
  product: string | null;
  metric: string;
  operator: "gt" | "gte" | "lt" | "lte";
  threshold: number;
  severity: string;
  enabled: boolean;
};

type Dashboard = {
  period_days: number;
  total_alerts: number;
  open_alerts: number;
  active_alerts: number;
  resolved_alerts: number;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
  average_resolution_hours: number | null;
  detection_policy: {
    threshold: string;
    statistical: string;
    ai_decides_alert_existence: boolean;
  };
  notification_channels: {
    in_app: boolean;
    email: boolean;
    webhook: boolean;
  };
  delivery_stats: Record<string, number>;
  feedback_counts: Record<string, number>;
  false_positive_rate: number | null;
};

type Notification = {
  id: number;
  alert_id: number;
  message: string;
  read: boolean;
  created_at: string;
};

const styles = {
  page: {
    minHeight: "100vh",
    padding: "32px clamp(16px, 4vw, 56px)",
    background: "#f5f7fb",
    color: "#172033",
    fontFamily: "Arial, sans-serif",
  } as const,
  panel: {
    background: "#fff",
    border: "1px solid #e1e6ef",
    borderRadius: 12,
    padding: 20,
    boxShadow: "0 2px 8px #15233b0b",
  } as const,
  input: {
    width: "100%",
    padding: "10px 12px",
    border: "1px solid #cbd3df",
    borderRadius: 7,
    background: "#fff",
    color: "#172033",
  } as const,
  button: {
    padding: "10px 14px",
    border: 0,
    borderRadius: 7,
    color: "#fff",
    background: "#2259bd",
    cursor: "pointer",
    fontWeight: 600,
  } as const,
};

export default function MonitoringPage() {
  const [token, setToken] = useState(
    () => (typeof window === "undefined" ? "" : window.localStorage.getItem("monitoring-token") ?? ""),
  );
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "signup" | "bootstrap" | "forgot">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [plants, setPlants] = useState<Plant[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [users, setUsers] = useState<UserDirectoryItem[]>([]);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [usersByPlant, setUsersByPlant] = useState<Record<number, UserDirectoryItem[]>>({});
  const [historyByAlert, setHistoryByAlert] = useState<Record<number, AlertHistoryItem[]>>({});
  const [targetUserId, setTargetUserId] = useState("");
  const [targetPlantIds, setTargetPlantIds] = useState<number[]>([]);
  const [targetFactoryIds, setTargetFactoryIds] = useState<number[]>([]);
  const [targetDepartmentIds, setTargetDepartmentIds] = useState<number[]>([]);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState<"operator" | "viewer">("operator");
  const [selectedPlant, setSelectedPlant] = useState("");
  const [selectedLine, setSelectedLine] = useState("");
  const [plantName, setPlantName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [factoryName, setFactoryName] = useState("");
  const [departmentName, setDepartmentName] = useState("");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [selectedFactoryId, setSelectedFactoryId] = useState("");
  const [selectedDepartmentId, setSelectedDepartmentId] = useState("");
  const [lineName, setLineName] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [granularity, setGranularity] = useState("day");
  const [interval, setInterval] = useState("1");
  const [unit, setUnit] = useState("days");
  const [ruleName, setRuleName] = useState("");
  const [ruleMetric, setRuleMetric] = useState("defect_rate");
  const [ruleThreshold, setRuleThreshold] = useState("5");
  const [ruleProduct, setRuleProduct] = useState("");
  const [filterType, setFilterType] = useState("");
  const canOperate = user?.role === "admin" || user?.role === "operator";
  const selectedPlantInfo = useMemo(
    () => plants.find((plant) => String(plant.id) === selectedPlant),
    [plants, selectedPlant],
  );

  useEffect(() => {
    const syncToken = () => {
      const nextToken = window.localStorage.getItem("monitoring-token") ?? "";
      setToken(nextToken);
      if (!nextToken) {
        setUser(null);
        setPlants([]);
        setAlerts([]);
      }
    };

    window.addEventListener(AUTH_CHANGED_EVENT, syncToken);
    window.addEventListener("storage", syncToken);
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, syncToken);
      window.removeEventListener("storage", syncToken);
    };
  }, []);

  const request = useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const response = await fetch(`${API_URL}${path}`, {
        ...init,
        headers: {
          ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload?.detail;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? response.statusText));
      }

      return payload as T;
    },
    [token],
  );

  async function updateProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const updated = await request<User>("/api/monitoring/auth/me", {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName.trim() || user?.display_name }),
      });
      setUser(updated);
      setMessage("Đã cập nhật hồ sơ.");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await request("/api/monitoring/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      window.localStorage.removeItem("monitoring-token");
      notifyAuthChanged();
      setToken("");
      setUser(null);
      setError("Mật khẩu đã đổi; mọi phiên bị thu hồi. Hãy đăng nhập lại.");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function revokeSession(session: AuthSession) {
    try {
      await request(`/api/monitoring/auth/sessions/${session.id}`, { method: "DELETE" });
      if (session.current) {
        signOut();
      } else {
        await refresh();
      }
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  const refresh = useCallback(async () => {
    if (!token) return;
    const [plantData, datasetData, ruleData, alertData, notificationData, dashboardData, userData, organizationData, factoryData, departmentData, sessionData] =
      await Promise.all([
        request<Plant[]>("/api/monitoring/plants"),
        request<Dataset[]>("/api/monitoring/datasets"),
        request<Rule[]>("/api/monitoring/rules"),
        request<AlertItem[]>(`/api/monitoring/alerts${filterType ? `?alert_type=${filterType}` : ""}`),
        request<Notification[]>("/api/monitoring/notifications"),
        request<Dashboard>("/api/monitoring/dashboard"),
        request<UserDirectoryItem[]>("/api/monitoring/users"),
        request<Organization[]>("/api/monitoring/organizations"),
        request<Factory[]>("/api/monitoring/factories"),
        request<Department[]>("/api/monitoring/departments"),
        request<AuthSession[]>("/api/monitoring/auth/sessions"),
      ]);
    setPlants(plantData);
    setOrganizations(organizationData);
    setFactories(factoryData);
    setDepartments(departmentData);
    setSessions(sessionData);
    setDatasets(datasetData);
    setRules(ruleData);
    setAlerts(alertData);
    setNotifications(notificationData);
    setDashboard(dashboardData);
    setUsers(userData);
    setSelectedOrganizationId((current) => current || String(organizationData[0]?.id ?? ""));
    setSelectedFactoryId((current) => current || String(factoryData[0]?.id ?? ""));
    const userScopes = await Promise.all(
      [...new Set(alertData.map((alert) => alert.plant_id))].map(async (plantId) => [
        plantId,
        await request<UserDirectoryItem[]>(`/api/monitoring/users?plant_id=${plantId}`),
      ] as const),
    );
    setUsersByPlant(Object.fromEntries(userScopes));
    setSelectedPlant((current) => current || String(plantData[0]?.id ?? ""));
  }, [filterType, request, token]);

  useEffect(() => {
    if (!token) return;
    request<User>("/api/monitoring/auth/me")
      .then(setUser)
      .then(refresh)
      .catch((cause: Error) => {
        window.localStorage.removeItem("monitoring-token");
        notifyAuthChanged();
        setToken("");
        setUser(null);
        setError(cause.message);
      });
  }, [refresh, request, token]);

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      if (authMode === "forgot") {
        await request("/api/monitoring/auth/forgot-password", {
          method: "POST",
          body: JSON.stringify({ email }),
        });
        setMessage("Nếu tài khoản tồn tại, hướng dẫn đặt lại mật khẩu sẽ được gửi.");
        return;
      }
      const endpoint =
        authMode === "signup"
          ? "/api/monitoring/auth/signup"
          : authMode === "bootstrap"
            ? "/api/monitoring/auth/bootstrap-admin"
            : "/api/monitoring/auth/login";
      const body = {
        email,
        password,
        ...(authMode === "signup" || authMode === "bootstrap" ? { display_name: displayName } : {}),
        ...(authMode === "bootstrap" ? { bootstrap_token: bootstrapToken } : {}),
      };
      const result = await request<{ access_token: string; user: User }>(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      });
      window.localStorage.setItem("monitoring-token", result.access_token);
      notifyAuthChanged();
      setToken(result.access_token);
      setUser(result.user);
      setMessage("Đăng nhập thành công.");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function createPlant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      if (!selectedFactoryId) {
        throw new Error("Hãy chọn Factory trước khi tạo Plant.");
      }
      await request("/api/monitoring/plants", {
        method: "POST",
        body: JSON.stringify({
          name: plantName,
          timezone: "Asia/Ho_Chi_Minh",
          factory_id: Number(selectedFactoryId),
          department_id: selectedDepartmentId ? Number(selectedDepartmentId) : null,
        }),
      });
      setPlantName("");
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function loadUserPlants(userId: number) {
    try {
      const assigned = await request<OrganizationScope>(`/api/monitoring/users/${userId}/organization-scope`);
      setTargetPlantIds(assigned.plant_ids);
      setTargetFactoryIds(assigned.factory_ids);
      setTargetDepartmentIds(assigned.department_ids);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function updateManagedUser(
    userId: number,
    patch: { role?: User["role"]; is_active?: boolean },
  ) {
    try {
      await request(`/api/monitoring/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({
          role: patch.role ?? users.find((item) => item.id === userId)?.role,
          is_active: patch.is_active,
        }),
      });
      await refresh();
      await loadUserPlants(userId);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function createManagedUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const created = await request<UserDirectoryItem>("/api/monitoring/users", {
        method: "POST",
        body: JSON.stringify({
          email: newUserEmail,
          display_name: newUserName,
          password: newUserPassword,
          role: newUserRole,
        }),
      });
      setTargetUserId(String(created.id));
      setTargetPlantIds([]);
      setTargetFactoryIds([]);
      setTargetDepartmentIds([]);
      setNewUserEmail("");
      setNewUserName("");
      setNewUserPassword("");
      await refresh();
      setMessage("Đã tạo người dùng. Hãy cấp Plant access trước khi họ sử dụng hệ thống.");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function saveUserPlants() {
    if (!targetUserId) return;
    try {
      await request(`/api/monitoring/users/${targetUserId}/organization-scope`, {
        method: "PUT",
        body: JSON.stringify({
          factory_ids: targetFactoryIds,
          department_ids: targetDepartmentIds,
          plant_ids: targetPlantIds,
        }),
      });
      await refresh();
      setMessage("Đã cập nhật Factory/Department/Plant access; quyền có hiệu lực ngay.");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function createOrganization(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      try {
        const created = await request<Organization>("/api/monitoring/organizations", {
          method: "POST",
          body: JSON.stringify({ name: organizationName }),
        });
        setOrganizationName("");
        setSelectedOrganizationId(String(created.id));
        setSelectedFactoryId("");
        setSelectedDepartmentId("");
        await refresh();
        setSelectedFactoryId("");
      } catch (cause) {
        setError((cause as Error).message);
      }
  }

  async function createFactory(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      try {
        if (!selectedOrganizationId) throw new Error("Hãy chọn Organization.");
        const created = await request<Factory>("/api/monitoring/factories", {
          method: "POST",
          body: JSON.stringify({
            organization_id: Number(selectedOrganizationId),
            name: factoryName,
          }),
        });
        setFactoryName("");
        setSelectedFactoryId(String(created.id));
        await refresh();
      } catch (cause) {
        setError((cause as Error).message);
      }
  }

  async function createDepartment(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      try {
        if (!selectedFactoryId) throw new Error("Hãy chọn Factory.");
        const created = await request<Department>("/api/monitoring/departments", {
          method: "POST",
          body: JSON.stringify({
            factory_id: Number(selectedFactoryId),
            name: departmentName,
          }),
        });
        setDepartmentName("");
        setSelectedDepartmentId(String(created.id));
        await refresh();
      } catch (cause) {
        setError((cause as Error).message);
    }
  }

  async function createLine(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedPlant) return;
    try {
      await request("/api/monitoring/lines", {
        method: "POST",
        body: JSON.stringify({ plant_id: Number(selectedPlant), name: lineName }),
      });
      setLineName("");
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function uploadSnapshot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!files?.length || !selectedPlant) return;
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    form.append("plant_id", selectedPlant);
    if (selectedLine) form.append("line_id", selectedLine);
    form.append("granularity", granularity);
    form.append("schedule_interval", interval);
    form.append("schedule_unit", unit);
    setError("");
    try {
      const result = await request<{ alerts_detected: number; rows_merged: number }>(
        "/api/monitoring/upload",
        { method: "POST", body: form },
      );
      setMessage(`Snapshot đã lưu: ${result.rows_merged} dòng; đã phát hiện ${result.alerts_detected} cảnh báo.`);
      setFiles(null);
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function createRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedPlant) return;
    try {
      await request("/api/monitoring/rules", {
        method: "POST",
        body: JSON.stringify({
          name: ruleName,
          plant_id: Number(selectedPlant),
          line_id: selectedLine ? Number(selectedLine) : null,
          product: ruleProduct || null,
          metric: ruleMetric,
          operator: "gt",
          threshold: Number(ruleThreshold),
          severity: "high",
        }),
      });
      setRuleName("");
      setMessage("Đã tạo ngưỡng. Ngưỡng được đánh giá bằng Python khi có snapshot mới.");
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function updateAlert(
    alertId: number,
    patch: {
      status?: AlertItem["status"];
      snoozed_until?: string;
      note?: string;
      assigned_to?: number | null;
      feedback?: "useful" | "false_positive" | "duplicate";
    },
  ) {
    try {
      await request(`/api/monitoring/alerts/${alertId}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function loadAlertHistory(alertId: number) {
    try {
      const history = await request<AlertHistoryItem[]>(`/api/monitoring/alerts/${alertId}/history`);
      setHistoryByAlert((current) => ({ ...current, [alertId]: history }));
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function updateSchedule(
    datasetId: number,
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      await request(`/api/monitoring/datasets/${datasetId}/schedule`, {
        method: "PATCH",
        body: JSON.stringify({
          interval: Number(values.get("interval")),
          unit: String(values.get("unit")),
          enabled: values.get("enabled") === "on",
        }),
      });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function deleteDataset(dataset: Dataset) {
    if (!window.confirm(`Xóa snapshot "${dataset.name}"? Cảnh báo đã tạo sẽ được giữ lại.`)) return;
    try {
      await request(`/api/monitoring/datasets/${dataset.id}`, { method: "DELETE" });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function updateRule(rule: Rule, patch: Partial<Rule>) {
    const current = {
      name: rule.name,
      plant_id: rule.plant_id,
      line_id: rule.line_id,
      product: rule.product,
      metric: rule.metric,
      operator: rule.operator,
      threshold: rule.threshold,
      severity: rule.severity,
      enabled: rule.enabled,
    };
    try {
      await request(`/api/monitoring/rules/${rule.id}`, {
        method: "PATCH",
        body: JSON.stringify({ ...current, ...patch }),
      });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function renamePlant(plant: Plant) {
    const name = window.prompt("Tên nhà máy mới", plant.name)?.trim();
    if (!name || name === plant.name) return;
    try {
      await request(`/api/monitoring/plants/${plant.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function renameLine(line: Plant["lines"][number]) {
    const name = window.prompt("Tên dây chuyền mới", line.name)?.trim();
    if (!name || name === line.name) return;
    try {
      await request(`/api/monitoring/lines/${line.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function markRead(id: number) {
    try {
      await request(`/api/monitoring/notifications/${id}/read`, { method: "POST" });
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function setEmailNotifications(enabled: boolean) {
    try {
      const updated = await request<{ email_notifications: boolean }>(
        "/api/monitoring/notifications/preferences",
        { method: "PATCH", body: JSON.stringify({ enabled }) },
      );
      setUser((current) => current ? { ...current, email_notifications: updated.email_notifications } : current);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  function signOut() {
    window.localStorage.removeItem("monitoring-token");
    notifyAuthChanged();
    setToken("");
    setUser(null);
    setPlants([]);
    setAlerts([]);
  }

  if (!token || !user) {
    return (
      <main className="monitoring-page" style={styles.page}>
        <section style={{ ...styles.panel, maxWidth: 480, margin: "8vh auto" }}>
          <Link href="/" style={{ color: "#2259bd" }}>← Về trang phân tích</Link>
          <h1>Intelligent Monitoring</h1>
          <p>Đăng nhập để quản lý nhà máy, ngưỡng và cảnh báo.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
            {(["login", "signup", "bootstrap", "forgot"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                style={{ ...styles.button, background: authMode === mode ? "#2259bd" : "#6b778b" }}
                onClick={() => setAuthMode(mode)}
              >
                {mode === "login" ? "Đăng nhập" : mode === "signup" ? "Đăng ký viewer" : mode === "bootstrap" ? "Khởi tạo admin" : "Quên mật khẩu"}
              </button>
            ))}
          </div>
          <form onSubmit={submitAuth} style={{ display: "grid", gap: 12 }}>
            {(authMode === "signup" || authMode === "bootstrap") && (
              <label>Tên hiển thị<input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} style={styles.input} /></label>
            )}
            <label>Email<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} style={styles.input} /></label>
            {authMode !== "forgot" && <label>Mật khẩu (tối thiểu 12 ký tự)<input required type="password" minLength={12} autoComplete={authMode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} style={styles.input} /></label>}
            {authMode === "bootstrap" && (
              <label>Bootstrap token<input required type="password" value={bootstrapToken} onChange={(event) => setBootstrapToken(event.target.value)} style={styles.input} /></label>
            )}
            <button style={styles.button}>{authMode === "login" ? "Đăng nhập" : authMode === "signup" ? "Tạo tài khoản viewer" : authMode === "bootstrap" ? "Tạo admin đầu tiên" : "Gửi liên kết đặt lại"}</button>
          </form>
          <p style={{ color: "#68758a", fontSize: 13 }}>
            Đăng ký công khai chỉ cấp quyền viewer. Admin đầu tiên cần ALERT_BOOTSTRAP_TOKEN; đặt ALERT_AUTH_SECRET tối thiểu 32 ký tự ở backend.
          </p>
          {error && <p role="alert" style={{ color: "#b42318" }}>{error}</p>}
        </section>
      </main>
    );
  }

  return (
    <main className="monitoring-page" style={styles.page}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div>
          <Link href="/" style={{ color: "#2259bd" }}>← Về phân tích</Link>
          <h1 style={{ marginBottom: 4 }}>Intelligent Monitoring</h1>
          <span>{user.display_name} · {user.role}</span>
        </div>
      </header>
      {error && <p role="alert" style={{ color: "#b42318", ...styles.panel }}>{error}</p>}
      {message && <p role="status" style={{ color: "#126b3a", ...styles.panel }}>{message}</p>}

      <section style={{ ...styles.panel, margin: "16px 0" }}>
        <h2>Tài khoản và phiên đăng nhập</h2>
        <form onSubmit={updateProfile} style={{ display: "flex", gap: 8, maxWidth: 520 }}>
          <label style={{ flex: 1 }}>Tên hiển thị<input required value={displayName || user.display_name} onChange={(event) => setDisplayName(event.target.value)} style={styles.input} /></label>
          <button style={styles.button}>Lưu hồ sơ</button>
        </form>
        <form onSubmit={changePassword} style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          <label>Mật khẩu hiện tại<input required minLength={12} type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} style={styles.input} /></label>
          <label>Mật khẩu mới<input required minLength={12} type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} style={styles.input} /></label>
          <button style={styles.button}>Đổi mật khẩu và đăng xuất phiên</button>
        </form>
        <h3>Phiên đang hoạt động</h3>
        <ul>{sessions.map((session) => <li key={session.id}>
          {session.current ? "Phiên hiện tại" : "Phiên khác"} · {session.user_agent ?? "Client không xác định"} · hết hạn {new Date(session.expires_at).toLocaleString()}
          <button type="button" onClick={() => void revokeSession(session)}>Thu hồi</button>
        </li>)}</ul>
      </section>

      {dashboard && (
        <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12, margin: "20px 0" }}>
          {[
            ["Cảnh báo 30 ngày", dashboard.total_alerts],
            ["Đang mở", dashboard.open_alerts],
            ["Đang xử lý", dashboard.active_alerts],
            ["Đã giải quyết", dashboard.resolved_alerts],
            ["Threshold", dashboard.by_type.threshold ?? 0],
            ["Statistical", dashboard.by_type.statistical ?? 0],
          ].map(([label, value]) => (
            <div key={label} style={styles.panel}><strong style={{ fontSize: 26 }}>{value}</strong><div>{label}</div></div>
          ))}
        </section>
      )}

      <section style={{ ...styles.panel, marginBottom: 16 }}>
        <h2>Chính sách phát hiện</h2>
        <p>Threshold alert: {dashboard?.detection_policy.threshold}. Statistical anomaly: {dashboard?.detection_policy.statistical}.</p>
        <p><strong>AI không quyết định cảnh báo có tồn tại hay không</strong>; điều kiện được tính và xác nhận bởi Python. Trạng thái chỉ số detector: {dashboard?.detection_policy.ai_decides_alert_existence ? "không an toàn" : "Python-only"}.</p>
        <p>Thời gian xử lý trung bình: {dashboard?.average_resolution_hours == null ? "chưa có dữ liệu" : `${dashboard.average_resolution_hours.toFixed(1)} giờ`}.</p>
        <p>Đánh giá: {dashboard?.feedback_counts.useful ?? 0} hữu ích · {dashboard?.feedback_counts.false_positive ?? 0} false positive · Tỷ lệ false positive trên cảnh báo đã đánh giá: {dashboard?.false_positive_rate == null ? "chưa có dữ liệu" : `${(dashboard.false_positive_rate * 100).toFixed(1)}%`}.</p>
        <p>Kênh thông báo: in-app {dashboard?.notification_channels.in_app ? "bật" : "tắt"} · Email/SMTP {dashboard?.notification_channels.email ? "đã cấu hình" : "chưa cấu hình"} · Webhook HTTPS {dashboard?.notification_channels.webhook ? "đã cấu hình" : "chưa cấu hình"}.</p>
        <label><input type="checkbox" checked={Boolean(user.email_notifications)} onChange={(event) => setEmailNotifications(event.target.checked)} /> Nhận email cảnh báo đến {user.email}</label>
      </section>

      <section style={{ ...styles.panel, marginBottom: 16 }}>
        <h2>Organization → Factory → Department / Plant → Production Line</h2>
        {canOperate && (
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
            {user.role === "admin" && <>
              <form onSubmit={createOrganization} style={{ display: "flex", gap: 8 }}>
                <input aria-label="Tên Organization" placeholder="Tên Organization" required value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} style={styles.input} />
                <button style={styles.button}>Tạo Organization</button>
              </form>
              <form onSubmit={createFactory} style={{ display: "flex", gap: 8 }}>
                <select required value={selectedOrganizationId} onChange={(event) => { setSelectedOrganizationId(event.target.value); setSelectedFactoryId(""); setSelectedDepartmentId(""); }} style={styles.input}>
                  <option value="">Chọn Organization</option>
                  {organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                <input aria-label="Tên Factory" placeholder="Tên Factory" required value={factoryName} onChange={(event) => setFactoryName(event.target.value)} style={styles.input} />
                <button disabled={!selectedOrganizationId} style={styles.button}>Tạo Factory</button>
              </form>
              <form onSubmit={createDepartment} style={{ display: "flex", gap: 8 }}>
                <input aria-label="Tên Department" placeholder="Tên Department" required value={departmentName} onChange={(event) => setDepartmentName(event.target.value)} style={styles.input} />
                <button disabled={!selectedFactoryId} style={styles.button}>Tạo Department</button>
              </form>
              <form onSubmit={createPlant} style={{ display: "flex", gap: 8 }}>
                <input aria-label="Tên nhà máy" placeholder="Tên nhà máy" required value={plantName} onChange={(event) => setPlantName(event.target.value)} style={styles.input} />
                <button disabled={!selectedFactoryId} style={styles.button}>Tạo Plant</button>
              </form>
              <label>Factory cho Department/Plant
                <select value={selectedFactoryId} onChange={(event) => { setSelectedFactoryId(event.target.value); setSelectedDepartmentId(""); }} style={styles.input}>
                  <option value="">Chọn Factory</option>
                  {factories.filter((item) => !selectedOrganizationId || item.organization_id === Number(selectedOrganizationId)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label>Department của Plant (tùy chọn)
                <select value={selectedDepartmentId} onChange={(event) => setSelectedDepartmentId(event.target.value)} style={styles.input}>
                  <option value="">Không thuộc Department</option>
                  {departments.filter((item) => item.factory_id === Number(selectedFactoryId)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
            </>}
            <form onSubmit={createLine} style={{ display: "flex", gap: 8 }}>
              <input aria-label="Tên dây chuyền" placeholder="Tên dây chuyền" required value={lineName} onChange={(event) => setLineName(event.target.value)} style={styles.input} />
              <button disabled={!selectedPlant} style={styles.button}>Thêm dây chuyền</button>
            </form>
          </div>
        )}
        <label style={{ display: "block", maxWidth: 420, marginTop: 16 }}>
          Scope đang chọn
          <select value={selectedPlant} onChange={(event) => { setSelectedPlant(event.target.value); setSelectedLine(""); }} style={styles.input}>
            <option value="">Chọn nhà máy</option>
            {plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name}</option>)}
          </select>
        </label>
        <ul>{plants.map((plant) => <li key={plant.id}>{plant.name} — {plant.lines.map((line) => line.name).join(", ") || "chưa có dây chuyền"}{canOperate && <> <button type="button" onClick={() => renamePlant(plant)}>Đổi tên nhà máy</button> {plant.lines.map((line) => <button type="button" key={line.id} onClick={() => renameLine(line)}>Đổi tên {line.name}</button>)}</>}</li>)}</ul>
      </section>

      {user.role === "admin" && (
        <section style={{ ...styles.panel, marginBottom: 16 }}>
          <h2>Người dùng và Plant access</h2>
          <form onSubmit={createManagedUser} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 10, alignItems: "end" }}>
            <label>Tên<input required value={newUserName} onChange={(event) => setNewUserName(event.target.value)} style={styles.input} /></label>
            <label>Email<input required type="email" value={newUserEmail} onChange={(event) => setNewUserEmail(event.target.value)} style={styles.input} /></label>
            <label>Mật khẩu<input required minLength={12} type="password" value={newUserPassword} onChange={(event) => setNewUserPassword(event.target.value)} style={styles.input} /></label>
            <label>Vai trò<select value={newUserRole} onChange={(event) => setNewUserRole(event.target.value as "operator" | "viewer")} style={styles.input}><option value="operator">Operator</option><option value="viewer">Viewer</option></select></label>
            <button style={styles.button}>Tạo tài khoản</button>
          </form>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 12, marginTop: 16 }}>
            <label>Chọn người dùng<select value={targetUserId} onChange={(event) => { setTargetUserId(event.target.value); setTargetPlantIds([]); if (event.target.value) void loadUserPlants(Number(event.target.value)); }} style={styles.input}><option value="">Chọn operator/viewer</option>{users.filter((item) => item.role !== "admin").map((item) => <option key={item.id} value={item.id}>{item.display_name} ({item.role})</option>)}</select></label>
            {targetUserId && <label>Vai trò<select value={users.find((item) => item.id === Number(targetUserId))?.role ?? "viewer"} onChange={(event) => void updateManagedUser(Number(targetUserId), { role: event.target.value as User["role"] })} style={styles.input}><option value="operator">Operator</option><option value="viewer">Viewer</option></select></label>}
            {targetUserId && <label>Trạng thái tài khoản<select value={users.find((item) => item.id === Number(targetUserId))?.is_active === false ? "inactive" : "active"} onChange={(event) => void updateManagedUser(Number(targetUserId), { is_active: event.target.value === "active" })} style={styles.input}><option value="active">Active</option><option value="inactive">Inactive</option></select></label>}
          </div>
          {targetUserId && <fieldset style={{ border: "1px solid #d5dce7", borderRadius: 8, marginTop: 12 }}>
            <legend>Phạm vi Factory / Department / Plant (cần cấp đủ cấp tổ chức)</legend>
            <strong>Factory</strong>
            {factories.map((factory) => <label key={factory.id} style={{ display: "block", padding: 4 }}><input type="checkbox" checked={targetFactoryIds.includes(factory.id)} onChange={(event) => setTargetFactoryIds((current) => event.target.checked ? [...current, factory.id] : current.filter((id) => id !== factory.id))} /> {factory.name}</label>)}
            <strong>Department</strong>
            {departments.map((department) => <label key={department.id} style={{ display: "block", padding: 4 }}><input type="checkbox" checked={targetDepartmentIds.includes(department.id)} onChange={(event) => setTargetDepartmentIds((current) => event.target.checked ? [...current, department.id] : current.filter((id) => id !== department.id))} /> {department.name}</label>)}
            <strong>Plant</strong>
            {plants.map((plant) => <label key={plant.id} style={{ display: "block", padding: 4 }}><input type="checkbox" checked={targetPlantIds.includes(plant.id)} onChange={(event) => setTargetPlantIds((current) => event.target.checked ? [...current, plant.id] : current.filter((id) => id !== plant.id))} /> {plant.name}</label>)}
            {!plants.length && <p>Tạo Plant đã ánh xạ tổ chức trước khi cấp quyền.</p>}
            <button type="button" style={styles.button} onClick={saveUserPlants}>Lưu phạm vi tổ chức</button>
          </fieldset>}
          <p>Thu hồi Plant sẽ chặn request kế tiếp ngay lập tức; người dùng không cần đăng xuất.</p>
        </section>
      )}

      {canOperate && (
        <section style={{ ...styles.panel, marginBottom: 16 }}>
          <h2>Lưu snapshot và đánh giá ngay</h2>
          <form onSubmit={uploadSnapshot} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 12, alignItems: "end" }}>
            <label>File CSV/XLSX/PDF/Word<input required multiple type="file" accept=".csv,.xlsx,.xls,.pdf,.docx" onChange={(event) => setFiles(event.target.files)} style={styles.input} /></label>
            <label>Dây chuyền<select value={selectedLine} onChange={(event) => setSelectedLine(event.target.value)} style={styles.input}><option value="">Toàn nhà máy</option>{selectedPlantInfo?.lines.map((line) => <option key={line.id} value={line.id}>{line.name}</option>)}</select></label>
            <label>Kỳ tổng hợp<select value={granularity} onChange={(event) => setGranularity(event.target.value)} style={styles.input}><option value="day">Ngày</option><option value="week">Tuần</option><option value="month">Tháng</option></select></label>
            <label>Chạy lại mỗi<input type="number" min="1" max="52" value={interval} onChange={(event) => setInterval(event.target.value)} style={styles.input} /></label>
            <label>Nhịp<select value={unit} onChange={(event) => setUnit(event.target.value)} style={styles.input}><option value="hours">Giờ</option><option value="days">Ngày (mặc định)</option><option value="weeks">Tuần</option></select></label>
            <button disabled={!selectedPlant || !files?.length} style={styles.button}>Lưu và chạy kiểm tra</button>
          </form>
          <p style={{ color: "#68758a" }}>Snapshot được lưu trong PostgreSQL để lịch chạy tiếp tục hoạt động sau khi tiến trình khởi động lại. File mới kích hoạt đánh giá ngay.</p>
        </section>
      )}

      {canOperate && (
        <section style={{ ...styles.panel, marginBottom: 16 }}>
          <h2>Threshold Alert</h2>
          <form onSubmit={createRule} style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 12, alignItems: "end" }}>
            <label>Tên rule<input required value={ruleName} onChange={(event) => setRuleName(event.target.value)} style={styles.input} /></label>
            <label>Dây chuyền<select value={selectedLine} onChange={(event) => setSelectedLine(event.target.value)} style={styles.input}><option value="">Toàn nhà máy</option>{selectedPlantInfo?.lines.map((line) => <option key={line.id} value={line.id}>{line.name}</option>)}</select></label>
            <label>Sản phẩm (không bắt buộc)<input value={ruleProduct} onChange={(event) => setRuleProduct(event.target.value)} style={styles.input} /></label>
            <label>Metric<select value={ruleMetric} onChange={(event) => setRuleMetric(event.target.value)} style={styles.input}><option value="defect_rate">Tỷ lệ lỗi (%)</option><option value="production">Sản lượng</option><option value="defects">Số lỗi</option><option value="downtime">Downtime</option></select></label>
            <label>Ngưỡng lớn hơn<input type="number" step="any" value={ruleThreshold} onChange={(event) => setRuleThreshold(event.target.value)} style={styles.input} /></label>
            <button disabled={!selectedPlant} style={styles.button}>Tạo rule</button>
          </form>
          <h3>Rule hiện có</h3>
          {rules.map((rule) => <div key={rule.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "8px 0", borderTop: "1px solid #e1e6ef" }}><span>{rule.name} · {rule.metric} {rule.operator} {rule.threshold}{rule.product ? ` · ${rule.product}` : ""} · {rule.enabled ? "đang bật" : "đã tắt"}</span><button style={styles.button} onClick={() => updateRule(rule, { enabled: !rule.enabled })}>{rule.enabled ? "Tạm dừng" : "Bật lại"}</button><button style={{ ...styles.button, background: "#526176" }} onClick={() => { const value = window.prompt("Ngưỡng mới", String(rule.threshold)); if (value !== null && Number.isFinite(Number(value))) void updateRule(rule, { threshold: Number(value) }); }}>Sửa ngưỡng</button></div>)}
        </section>
      )}

      <section style={{ ...styles.panel, marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <h2>Cảnh báo</h2>
          <select aria-label="Lọc loại cảnh báo" value={filterType} onChange={(event) => setFilterType(event.target.value)} style={{ ...styles.input, maxWidth: 220 }}>
            <option value="">Tất cả</option><option value="threshold">Threshold</option><option value="statistical">Statistical anomaly</option>
          </select>
        </div>
        <div style={{ display: "grid", gap: 12 }}>
          {alerts.map((alert) => (
            <article key={alert.id} style={{ border: "1px solid #e1e6ef", borderLeft: `5px solid ${alert.severity === "critical" ? "#b42318" : alert.severity === "high" ? "#e16b16" : "#d3a213"}`, borderRadius: 8, padding: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <strong>{alert.title}</strong>
                <span>{alert.severity} · {alert.status} · {alert.alert_type}</span>
              </div>
              <p>{alert.summary}</p>
              {alert.ai_explanation && <p><strong>Diễn giải AI (không quyết định cảnh báo):</strong> {alert.ai_explanation}</p>}
              <p><strong>Scope:</strong> {alert.plant_name}{alert.line_name ? ` / ${alert.line_name}` : ""}{alert.scope_value ? ` / ${alert.scope_type}: ${alert.scope_value}` : ""} · <strong>Metric:</strong> {alert.metric} = {alert.observed_value}{alert.threshold_value != null ? ` (ngưỡng ${alert.threshold_value})` : ""}</p>
              <p><strong>Ảnh hưởng:</strong> sản lượng {alert.impact.production ?? 0}, lỗi {alert.impact.defects ?? 0} · <strong>Phát hiện:</strong> {new Date(alert.detected_at).toLocaleString()}</p>
              <details><summary>Bằng chứng và bước kiểm tra</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(alert.evidence, null, 2)}</pre><p>{alert.recommended_check}</p></details>
              <details onToggle={(event) => { if (event.currentTarget.open && !historyByAlert[alert.id]) void loadAlertHistory(alert.id); }}><summary>Lịch sử xử lý</summary>{(historyByAlert[alert.id] ?? []).map((item) => <p key={item.id}>{new Date(item.created_at).toLocaleString()} · {item.actor_name} · {item.event_type}: {item.from_status ?? "—"} → {item.to_status ?? "—"}{item.note ? ` · ${item.note}` : ""}</p>)}</details>
              {canOperate && <div style={{ display: "flex", gap: 8, marginTop: 10 }}><button style={{ ...styles.button, background: "#167447" }} onClick={() => updateAlert(alert.id, { feedback: "useful" })}>Hữu ích</button><button style={{ ...styles.button, background: "#9b3c37" }} onClick={() => updateAlert(alert.id, { feedback: "false_positive" })}>False positive</button><button style={{ ...styles.button, background: "#526176" }} onClick={() => updateAlert(alert.id, { feedback: "duplicate" })}>Trùng lặp</button></div>}
              {canOperate && <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                <select aria-label={`Phân công cảnh báo ${alert.id}`} value={alert.assigned_to ?? ""} onChange={(event) => updateAlert(alert.id, { assigned_to: event.target.value ? Number(event.target.value) : null })} style={{ ...styles.input, maxWidth: 240 }}>
                  <option value="">Chưa phân công</option>{(usersByPlant[alert.plant_id] ?? []).filter((item) => item.role !== "viewer").map((item) => <option key={item.id} value={item.id}>{item.display_name} ({item.role})</option>)}
                </select>
                <select aria-label={`Trạng thái cảnh báo ${alert.id}`} value={alert.status} onChange={(event) => { const status = event.target.value as AlertItem["status"]; if (status === "snoozed") { const until = window.prompt("Thời điểm kết thúc tạm hoãn (ISO 8601)", new Date(Date.now() + 60 * 60 * 1000).toISOString()); if (until) void updateAlert(alert.id, { status, snoozed_until: until }); } else void updateAlert(alert.id, { status }); }} style={{ ...styles.input, maxWidth: 190 }}>
                  {(["open", "acknowledged", "investigating", "snoozed", "resolved", "closed"] as const).map((nextStatus) => <option key={nextStatus} value={nextStatus}>{nextStatus}</option>)}
                </select>
              </div>}
            </article>
          ))}
          {!alerts.length && <p>Chưa có cảnh báo trong hệ thống.</p>}
        </div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: 16 }}>
        <div style={styles.panel}>
          <h2>Snapshot và lịch</h2>
          {datasets.map((dataset) => <div key={dataset.id} style={{ borderTop: "1px solid #e1e6ef", padding: "8px 0" }}><strong>{dataset.name}</strong> · {dataset.row_count} dòng<br /><small>Kiểm tra gần nhất: {dataset.last_checked_at ? new Date(dataset.last_checked_at).toLocaleString() : "chưa chạy"}</small>{canOperate && <><form onSubmit={(event) => updateSchedule(dataset.id, event)} style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}><input aria-label="Khoảng lịch" name="interval" type="number" min="1" max="52" defaultValue={dataset.schedule_interval} style={{ ...styles.input, maxWidth: 80 }} /><select aria-label="Đơn vị lịch" name="unit" defaultValue={dataset.schedule_unit} style={{ ...styles.input, maxWidth: 110 }}><option value="hours">Giờ</option><option value="days">Ngày</option><option value="weeks">Tuần</option></select><label><input name="enabled" type="checkbox" defaultChecked={dataset.enabled} /> Bật</label><button style={styles.button}>Lưu lịch</button></form>{user.role === "admin" && <button style={{ ...styles.button, background: "#9b3c37", marginTop: 6 }} onClick={() => deleteDataset(dataset)}>Xóa snapshot</button>}</>}</div>)}
          {!datasets.length && <p>Chưa có snapshot.</p>}
        </div>
        <div style={styles.panel}>
          <h2>Hộp thư in-app</h2>
          {notifications.slice(0, 20).map((notification) => <p key={notification.id} style={{ opacity: notification.read ? 0.6 : 1 }}>{notification.message} {!notification.read && <button style={{ ...styles.button, padding: "5px 8px" }} onClick={() => markRead(notification.id)}>Đã đọc</button>}</p>)}
          {!notifications.length && <p>Chưa có thông báo.</p>}
        </div>
      </section>
    </main>
  );
}
