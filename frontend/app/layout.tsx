import type { Metadata } from "next";
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
      <body>{children}</body>
    </html>
  );
}