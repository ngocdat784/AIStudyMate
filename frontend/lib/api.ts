import { notifyAuthChanged, redirectToLogin } from "./auth";

export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  let token: string | null = null;
  if (typeof window !== "undefined") {
    token = window.localStorage.getItem("monitoring-token");
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  return fetch(input, { ...init, headers }).then((response) => {
    const requestUrl =
      input instanceof Request
        ? input.url
        : typeof input === "string"
          ? input
          : input.toString();
    const requestPath =
      typeof window === "undefined"
        ? requestUrl
        : new URL(requestUrl, window.location.origin).pathname;
    if (
      response.status === 401 &&
      requestPath !== "/api/hardware/analyze"
    ) {
      if (typeof window !== "undefined") {
        if (token) {
          window.localStorage.removeItem("monitoring-token");
          notifyAuthChanged();
        }
      }
      redirectToLogin();
    }
    return response;
  });
}
