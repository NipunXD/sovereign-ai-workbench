import type { NextConfig } from "next";

const API_URL = process.env.WORKBENCH_API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // The API is proxied rather than called cross-origin. That keeps the refresh
  // cookie same-origin (so it can stay httpOnly and SameSite=Strict) and means
  // the browser never needs to know the API's address — which matters when the
  // same build is deployed behind a different hostname on a plant network.
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_URL}/api/v1/:path*` }];
  },
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default config;
