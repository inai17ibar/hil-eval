import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:8799", headless: true },
  webServer: {
    command:
      "UV_CACHE_DIR=/tmp/hil-eval-uv-cache uv run --offline --no-sync python ui/e2e/seed.py",
    cwd: "..",
    url: "http://127.0.0.1:8799/healthz",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
