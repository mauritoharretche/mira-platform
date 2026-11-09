import { defineConfig } from "@playwright/test";
import path from "node:path";
import os from "node:os";

const origin = process.env.MIRA_E2E_BASE_URL;
const artifacts = process.env.MIRA_E2E_ARTIFACT_DIR;
if (process.env.MIRA_RUN_BROWSER_TESTS !== "1" || !origin || !artifacts || !process.env.MIRA_E2E_PASSWORD) {
  throw new Error("Run the browser suite through the authorized Django LiveServerTestCase.");
}
const url = new URL(origin);
if (url.protocol !== "http:" || url.hostname !== "localhost" || !path.resolve(artifacts).startsWith(path.resolve(os.tmpdir()) + path.sep)) {
  throw new Error("Browser tests require an isolated loopback server and external temporary artifacts.");
}

export default defineConfig({
  testDir: "./e2e", timeout: 30_000, expect: { timeout: 8_000 },
  fullyParallel: false, workers: 1, retries: 0, forbidOnly: true,
  reporter: "list", outputDir: path.join(artifacts, "playwright-results"),
  use: { baseURL: origin, browserName: "chromium", trace: "off", video: "off", screenshot: "off" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
