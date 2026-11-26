import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests run against a RUNNING stack (FastAPI backend + `next start`), so they exercise the
 * real API. Point them at it with E2E_BASE_URL and tell them which dataset it serves with
 * E2E_MODE=real|demo (see README, "Frontend tests").
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
});
