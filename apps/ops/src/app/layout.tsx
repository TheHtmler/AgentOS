import type { Metadata, Viewport } from "next";
import { Instrument_Sans, Source_Serif_4 } from "next/font/google";

import "./globals.css";

const opsSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-ops-sans",
  display: "swap",
});

const opsDisplay = Source_Serif_4({
  subsets: ["latin"],
  variable: "--font-ops-display",
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
    <html lang="zh-CN" className={`${opsSans.variable} ${opsDisplay.variable}`}>
      <body>{children}</body>
    </html>
  );
}
