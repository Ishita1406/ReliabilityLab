import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real backend and a production build of the dashboard.
 *
 * Needs the local PostgreSQL (infra/docker-compose.yml) and `uv`. The global setup drops,
 * recreates, migrates, and seeds the dedicated `*_e2e` database (never the dev database).
 */
const API_PORT = 8010;
const WEB_PORT = 3010;
export const E2E_DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+psycopg://reliabilitylab:reliabilitylab_local_dev@127.0.0.1:5433/reliabilitylab_e2e";
const PATH = `${process.env.HOME}/.local/bin:${process.env.PATH}`;

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run uvicorn app.main:app --port ${API_PORT}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: { APP_DATABASE_URL: E2E_DATABASE_URL, PATH },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run build && npx next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { RELIABILITYLAB_API_URL: `http://127.0.0.1:${API_PORT}`, PATH },
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
