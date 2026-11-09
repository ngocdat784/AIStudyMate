import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI StudyMate",
  description:
    "AI-powered learning assistant for smarter studying",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <body>
        <nav
          style={{
            display: "flex",
            justifyContent: "flex-end",
            padding: "8px 20px",
            borderBottom: "1px solid #e1e6ef",
          }}
        >
          <Link href="/monitoring">Intelligent Monitoring</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}