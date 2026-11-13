import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies /api and /health to the CodeAtlas API, so the browser talks to one origin.
const api = process.env.CODEATLAS_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: api, changeOrigin: true },
      "/health": { target: api, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
