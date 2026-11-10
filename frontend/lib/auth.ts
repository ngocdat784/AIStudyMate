export const AUTH_CHANGED_EVENT = "monitoring-auth-changed";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
let loginRedirectStarted = false;

export function notifyAuthChanged() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  }
}

export function hasAuthToken() {
  return (
    typeof window !== "undefined" &&
    Boolean(window.localStorage.getItem("monitoring-token"))
  );
}

export function redirectToLogin() {
  if (typeof window === "undefined" || loginRedirectStarted) {
    return;
  }

  loginRedirectStarted = true;
  window.alert("Vui lòng đăng nhập để sử dụng chức năng này.");
  window.location.assign(new URL("/monitoring", window.location.origin));
}

export async function verifyCurrentUser<T>() {
  const token =
    typeof window === "undefined"
      ? null
      : window.localStorage.getItem("monitoring-token");
  if (!token) {
    return null;
  }

  const response = await fetch(`${API_URL}/api/monitoring/auth/me`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    if (response.status === 401) {
      window.localStorage.removeItem("monitoring-token");
      notifyAuthChanged();
      return null;
    }
    throw new Error("Không thể xác minh phiên đăng nhập.");
  }

  return (await response.json()) as T;
}
