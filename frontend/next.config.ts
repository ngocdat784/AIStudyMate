import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["*.trycloudflare.com"],
  async rewrites() {
    const backendUrl =
      process.env.BACKEND_INTERNAL_URL ??
      (process.env.NODE_ENV === "development"
        ? "http://127.0.0.1:8000"
        : "https://aistudymate-production.up.railway.app");
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
