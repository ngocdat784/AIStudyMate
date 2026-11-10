import type { Metadata } from "next";
import Link from "next/link";
import AuthNavigation from "./auth-navigation";
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