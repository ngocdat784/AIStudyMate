"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function ResetPasswordPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(
      () => setToken(new URLSearchParams(window.location.search).get("token") ?? ""),
      0,
    );
    return () => window.clearTimeout(timer);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API_URL}/api/monitoring/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail ?? response.statusText);
      setMessage("Đã đổi mật khẩu và thu hồi các phiên cũ. Hãy đăng nhập lại.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể đặt lại mật khẩu.");
    }
  }

  return (
    <main className="reset-password-page">
      <Link href="/monitoring">← Đăng nhập</Link>
      <h1>Đặt lại mật khẩu</h1>
      <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
        <label>Mật khẩu mới<input required minLength={12} type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} style={{ display: "block", width: "100%", padding: 10 }} /></label>
        <button type="submit" disabled={!token}>Đổi mật khẩu</button>
      </form>
      {!token && <p role="alert">Thiếu liên kết đặt lại hợp lệ.</p>}
      {error && <p role="alert" style={{ color: "#b42318" }}>{error}</p>}
      {message && <p role="status">{message}</p>}
    </main>
  );
}
