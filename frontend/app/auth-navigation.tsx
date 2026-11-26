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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

const navigationGroups = [
  {
    label: "Tổng quan",
    links: [
      { href: "/dashboard", label: "Tổng quan" },
      { href: "/reports", label: "Báo cáo" },
      { href: "/history", label: "Lịch sử" },
    ],
  },
  {
    label: "Phân tích",
    links: [
      { href: "/production", label: "Phân tích sản xuất" },
      { href: "/machine-intelligence", label: "Phân tích thiết bị" },
      { href: "/material-intelligence", label: "Phân tích vật liệu" },
      { href: "/predictive-intelligence", label: "Dự báo sản xuất" },
    ],
  },
  {
    label: "Chất lượng & điều hành",
    links: [
      { href: "/quality", label: "Phân tích chất lượng" },
      { href: "/root-cause", label: "Điều tra nguyên nhân" },
      { href: "/enterprise-intelligence", label: "Điều hành doanh nghiệp" },
    ],
  },
  {
    label: "Dữ liệu & vận hành",
    links: [
      { href: "/system-integration", label: "Nghiệm thu tích hợp" },
      { href: "/monitoring", label: "Giám sát" },
      { href: "/integrations", label: "Tích hợp dữ liệu" },
      { href: "/improvements", label: "Cải tiến" },
    ],
  },
] as const;

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

  useEffect(() => {
    function applyTheme(value: string | null) {
      const selectedTheme =
        value === "light" || value === "dark"
          ? value
          : window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark"
            : "light";
      document.documentElement.dataset.theme = selectedTheme;
    }

    applyTheme(window.localStorage.getItem("hardwareai-theme"));
    function handleThemeStorage(event: StorageEvent) {
      if (event.key === "hardwareai-theme") {
        applyTheme(event.newValue);
      }
    }

    window.addEventListener("storage", handleThemeStorage);
    return () => window.removeEventListener("storage", handleThemeStorage);
  }, []);

  function toggleTheme() {
    const nextTheme =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("hardwareai-theme", nextTheme);
  }

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
        {navigationGroups.map((group) => (
          <details className="site-nav-group" key={group.label}>
            <summary>{group.label}</summary>
            <div className="site-nav-menu">
              {group.links.map((item) => (
                <Link
                  href={item.href}
                  key={item.href}
                  onClick={handleProtectedNavigation}
                >
                  {item.label}
                </Link>
              ))}
            </div>
          </details>
        ))}
      </nav>
      <button
        className="site-theme-toggle"
        type="button"
        onClick={toggleTheme}
        aria-label="Chuyển đổi chế độ sáng và tối"
        title="Chuyển đổi chế độ sáng và tối"
      >
        <svg
          className="theme-icon-dark"
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M20.4 15.4A8.5 8.5 0 0 1 8.6 3.6 8.5 8.5 0 1 0 20.4 15.4Z" />
        </svg>
        <svg
          className="theme-icon-light"
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
        </svg>
        <span className="theme-label-dark">Tối</span>
        <span className="theme-label-light">Sáng</span>
      </button>
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
