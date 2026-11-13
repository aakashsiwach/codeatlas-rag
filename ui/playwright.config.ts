import { defineConfig } from "@playwright/test";

// The smoke test starts the API over the fixture repository with the offline LLM (no network, no keys)
// and the Vite dev server proxying to it. Set CODEATLAS_PYTHON to the interpreter with codeatlas installed.
const python = process.env.CODEATLAS_PYTHON ?? (process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python");
const apiPort = 8765;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:5173", viewport: { width: 1360, height: 860 } },
  webServer: [
    {
      command: `"${python}" e2e/serve_fixture.py ${apiPort}`,
      url: `http://127.0.0.1:${apiPort}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      env: { CODEATLAS_API: `http://127.0.0.1:${apiPort}` },
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
