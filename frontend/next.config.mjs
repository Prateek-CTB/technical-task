/**
 * The browser only talks to this Next.js server (same origin). `/api/v1/*` is proxied to the FastAPI
 * backend by src/proxy.ts, using the server-side env var BACKEND_URL (default http://127.0.0.1:8000).
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};
export default nextConfig;
