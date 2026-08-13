import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AgentOS Ops",
  description: "Operations console for AgentOS.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
