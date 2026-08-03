import type { Metadata, Viewport } from "next";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/ibm-plex-sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentOS",
  description: "A controllable agent runtime platform.",
  icons: {
    icon: [{ url: "/agentos-mark.svg", type: "image/svg+xml" }],
    apple: [{ url: "/agentos-mark.svg" }],
  },
};

// This is an app workspace, not a document page. Keep its mobile geometry stable.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="min-h-full font-sans antialiased">{children}</body>
    </html>
  );
}
