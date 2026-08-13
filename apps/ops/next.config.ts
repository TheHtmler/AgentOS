import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Ops console must never be CDN/nginx-cached as a stale prerender shell.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Cache-Control", value: "private, no-cache, no-store, max-age=0, must-revalidate" },
        ],
      },
    ];
  },
};

export default nextConfig;
