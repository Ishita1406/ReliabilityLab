// Capture real API responses from a backend seeded with `scripts/prepare_e2e_db.py` into
// src/test/fixtures (used by unit tests only; never shown by the app).
//
//   RELIABILITYLAB_API_URL=http://127.0.0.1:8010 node scripts/capture-fixtures.mjs
//
// It submits one review and one correction (as reviewer "fixture-reviewer") so the review
// history fixture is real API output. Run it only against the disposable e2e database.
import { mkdir, writeFile } from "node:fs/promises";

const base = (process.env.RELIABILITYLAB_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");
const out = new URL("../src/test/fixtures/", import.meta.url);

async function call(path, init) {
  const response = await fetch(base + path, init);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}
const post = (path, body) =>
  call(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

const experiments = await call("/api/experiments");
const byLabel = (label) => experiments.find((e) => e.name.startsWith(`[demo] ${label} `)).id;
const baseline = byLabel("baseline");
const noRetry = byLabel("no-retry");
const runs = await call(`/api/experiments/${baseline}/runs`);
const failed = runs.find((r) => r.verdict === "failed");
const passed = runs.find((r) => r.scenario_id.endsWith(".baseline"));

const first = await post(`/api/runs/${passed.run_id}/reviews`, {
  reviewer: "fixture-reviewer",
  label: "fail",
  blind: true,
  categories: { task_correctness: "present" },
});
await post(`/api/reviews/${first.id}/corrections`, { reason: "misread the answer", label: "pass" });

const fixtures = {
  "overview.json": await call("/api/overview"),
  "suite.json": await call("/api/suites/research-agent-sample/1.0.0"),
  "experiment.json": await call(`/api/experiments/${baseline}`),
  "experiment-pending.json": await call(`/api/experiments/${byLabel("pending")}`),
  "run-failed.json": await call(`/api/runs/${failed.run_id}`),
  "comparison.json": await call(`/api/comparisons?baseline=${baseline}&candidate=${noRetry}`),
  "run-reviews.json": await call(`/api/runs/${passed.run_id}/reviews`),
  "review-queue.json": await call(`/api/experiments/${baseline}/review-queue`),
};
await mkdir(out, { recursive: true });
for (const [name, data] of Object.entries(fixtures)) {
  await writeFile(new URL(name, out), JSON.stringify(data, null, 2) + "\n");
  console.log(`wrote src/test/fixtures/${name}`);
}
