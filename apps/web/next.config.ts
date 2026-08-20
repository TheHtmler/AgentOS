import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Deploys build into .next.new and swap it in after a successful build, so the
  // running server keeps serving the old build (scripts/macmini-deploy.sh).
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
};

export default nextConfig;
