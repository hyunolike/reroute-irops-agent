/** @type {import('next').NextConfig} */
const apiInternal = process.env.API_INTERNAL_URL || "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Compression would buffer the Server-Sent Events stream proxied below.
  compress: false,
  async rewrites() {
    // Same-origin /api/* -> ReRoute API (in production an ALB routes /api/* straight to the API).
    return [{ source: "/api/:path*", destination: `${apiInternal}/api/:path*` }];
  },
};

export default nextConfig;
