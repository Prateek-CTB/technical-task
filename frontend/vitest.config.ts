import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { pool: "vmThreads", exclude: ["e2e/**", "node_modules/**"], environment: "jsdom", setupFiles: ["./src/test/setup.ts"], globals: true, css: false },
});
