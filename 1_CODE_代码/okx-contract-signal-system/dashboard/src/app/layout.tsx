import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OKX Signal Desk",
  description: "Local trading signal dashboard for OKX contract signals",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
