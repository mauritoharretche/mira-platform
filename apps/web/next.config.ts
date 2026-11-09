import type { NextConfig } from "next";

// These are the complete operator proxy surface, never an arbitrary URL proxy.
const endpoints = [
  "auth/csrf", "auth/login", "auth/session", "auth/logout",
  "catalog/products", "catalog/products/:productId",
  "inventory/variants/:variantId/initial-receipt",
  "inventory/variants/:variantId/stock", "inventory/variants/:variantId/movements",
];

const nextConfig: NextConfig = {
  async rewrites() {
    const origin = new URL(process.env.MIRA_API_BASE_URL ?? "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(origin.protocol) || origin.username || origin.password ||
        origin.pathname !== "/" || origin.search || origin.hash ||
        !["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname)) {
      throw new Error("The operator demo requires a loopback API origin.");
    }
    return endpoints.map((endpoint) => ({
      source: `/api/v1/operator/${endpoint}`,
      destination: `${origin.origin}/api/v1/operator/${endpoint}`,
    }));
  },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "same-origin" },
    ] }];
  },
};

export default nextConfig;
