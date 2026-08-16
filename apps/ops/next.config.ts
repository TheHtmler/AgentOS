import type { NextConfig } from "next";

const NO_STORE = [
  { key: "Cache-Control", value: "private, no-cache, no-store, max-age=0, must-revalidate" },
  { key: "Pragma", value: "no-cache" },
  { key: "Expires", value: "0" },
] as const;

const nextConfig: NextConfig = {
  // HTML documents must revalidate; early `/` shells were the knowledge list.
  async headers() {
    return [
      { source: "/", headers: [...NO_STORE] },
      { source: "/login", headers: [...NO_STORE] },
      { source: "/overview", headers: [...NO_STORE] },
      { source: "/:path*", headers: [...NO_STORE] },
    ];
  },
  async redirects() {
    return [{ source: "/", destination: "/overview", permanent: false }];
  },
};

export default nextConfig;
