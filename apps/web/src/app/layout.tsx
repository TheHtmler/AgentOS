import type { Metadata, Viewport } from "next";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/ibm-plex-sans";

import { ThemeProvider } from "@/components/theme/theme-provider";
import { THEME_STORAGE_KEY } from "@/lib/theme";

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

const themeBootScript = `
(() => {
  try {
    const key = ${JSON.stringify(THEME_STORAGE_KEY)};
    const stored = localStorage.getItem(key);
    const theme =
      stored === "light" || stored === "dark"
        ? stored
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  } catch (_) {
    document.documentElement.dataset.theme = "light";
    document.documentElement.style.colorScheme = "light";
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body className="min-h-full font-sans antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
