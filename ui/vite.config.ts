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
    // Node 25+ defines its own localStorage global (undefined without --localstorage-file), which hides
    // jsdom's. Turn Node's off in the test workers so the tests use jsdom's storage, like a browser.
    execArgv: ["--no-experimental-webstorage"],
  },
});
