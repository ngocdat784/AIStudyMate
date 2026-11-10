"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  AUTH_CHANGED_EVENT,
  hasAuthToken,
  notifyAuthChanged,
  redirectToLogin,
  verifyCurrentUser,
} from "../lib/auth";

type AuthUser = {
  display_name: string;
  role: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export default function AuthNavigation() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [logoutError, setLogoutError] = useState("");

  const refreshUser = useCallback(async () => {
    try {
      setLogoutError("");
      setUser(await verifyCurrentUser<AuthUser>());
    } catch (error) {
      setLogoutError(
        error instanceof Error ? error.message : "Không thể xác minh đăng nhập.",
      );
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refreshUser(), 0);
    window.addEventListener(AUTH_CHANGED_EVENT, refreshUser);
    window.addEventListener("storage", refreshUser);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(AUTH_CHANGED_EVENT, refreshUser);
      window.removeEventListener("storage", refreshUser);
    };
  }, [refreshUser]);

  async function logout() {
    const token = window.localStorage.getItem("monitoring-token");
    if (!token) {
      setUser(null);
      return;
    }

    setLogoutError("");
    try {
      const response = await fetch(`${API_URL}/api/monitoring/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok && response.status !== 401) {
        throw new Error("Không thể đăng xuất. Vui lòng thử lại.");
      }
      window.localStorage.removeItem("monitoring-token");
      setUser(null);
      notifyAuthChanged();
    } catch (error) {
      setLogoutError(
        error instanceof Error ? error.message : "Không thể đăng xuất.",
      );
    }
  }

  function handleProtectedNavigation(
    event: React.MouseEvent<HTMLAnchorElement>,
  ) {
    if (hasAuthToken()) {
      return;
    }
    event.preventDefault();
    redirectToLogin();
  }

  return (
    <div className="site-navigation">
      <nav aria-label="Điều hướng chính">
        <Link href="/monitoring">Monitoring</Link>
        <Link href="/history" onClick={handleProtectedNavigation}>
          Lịch sử
        </Link>
        <Link href="/reports" onClick={handleProtectedNavigation}>
          Báo cáo
        </Link>
      </nav>
      {user ? (
        <div className="site-auth-actions">
          <span className="site-user-name">
            {user.display_name} · {user.role}
          </span>
          <button type="button" onClick={() => void logout()}>
            Đăng xuất
          </button>
        </div>
      ) : (
        <Link
          className="site-login-icon"
          href="/monitoring"
          aria-label="Đăng nhập"
          title="Đăng nhập"
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            width="22"
            height="22"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 21a8 8 0 0 0-16 0" />
            <circle cx="12" cy="8" r="4" />
          </svg>
        </Link>
      )}
      {logoutError && (
        <span className="site-auth-error" role="alert">
          {logoutError}
        </span>
      )}
    </div>
  );
}
