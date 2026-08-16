import type { Metadata, Viewport } from "next";
import { IBM_Plex_Sans } from "next/font/google";

import "./globals.css";

const opsSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-ops-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AgentOS Ops",
  description: "Operations console for AgentOS.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className={opsSans.variable}>
      <body>{children}</body>
    </html>
  );
}
