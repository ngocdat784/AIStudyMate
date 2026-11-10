import type { Metadata } from "next";
import Link from "next/link";
import AuthNavigation from "./auth-navigation";
import "./globals.css";

export const metadata: Metadata = {
  title: "HardwareAI — Phân tích dữ liệu sản xuất",
  description:
    "Phân tích KPI, chất lượng dữ liệu và xu hướng sản xuất với HardwareAI.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <body>
        <header className="site-header">
          <Link className="site-brand" href="/">
            HardwareAI
          </Link>
          <AuthNavigation />
        </header>
        {children}
      </body>
    </html>
  );
}