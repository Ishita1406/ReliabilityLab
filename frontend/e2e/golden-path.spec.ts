import { expect, type Page, test } from "@playwright/test";

// The Milestone 11 golden path on the `reliability-demo` benchmark, entirely through the UI and
// the real stack (API, PostgreSQL, Redis, Celery worker):
//   1 select the benchmark · 2 execute experiments · 3 runs and traces are persisted ·
//   4 outcomes are evaluated · 5 inspect a failed trace · 6 compare two agent configurations ·
//   7 detect the intentionally seeded regression (no-retry) · 8 submit a human review.

const SUITE = "Reliability demo benchmark (1.0.0)";

async function choose(page: Page, label: string, optionText: string) {
  const select = page.getByRole("form", { name: "New experiment" }).getByLabel(label);
  const value = await select
    .locator("option", { hasText: optionText })
    .first()
    .getAttribute("value");
  await select.selectOption(value!);
}

async function runExperiment(page: Page, name: string, agentLabel: string) {
  await page.goto("/experiments");
  const form = page.getByRole("form", { name: "New experiment" });
  await form.getByLabel("Name").fill(name);
  await choose(page, "Suite", SUITE);
  await choose(page, "Agent version", agentLabel);
  await form.getByRole("button", { name: "Create and queue" }).click();
  await expect(page).toHaveURL(/\/experiments\/exp_[0-9a-f]{32}$/);
  // Persisted and evaluated by the background worker: 16 scenarios x 1 repetition.
  await expect(page.getByText("16 of 16 runs stored · 16 evaluated")).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page.getByRole("region", { name: "Status" }).getByText("completed", { exact: true }),
  ).toBeVisible();
}

test("golden path: benchmark → experiments → failed trace → comparison → regression → review", async ({
  page,
}) => {
  test.setTimeout(240_000);

  // 1. Select the benchmark and see its controlled scenarios.
  await page.goto("/benchmarks");
  await page.getByRole("link", { name: "Reliability demo benchmark" }).click();
  await expect(page.getByRole("status")).toContainText("Showing 5 of 5 cases");

  // 2-4. Execute two agent configurations; runs, traces, and evaluations are stored.
  await runExperiment(page, "golden reference", "0.1.0 (deterministic/regex-extractor)");
  // The reference agent's one controlled failure (it reports a conflict instead of answering).
  const referenceFailures = page.getByRole("table", { name: "Failed and unresolved cases" });
  await expect(referenceFailures).toContainText("fetch-misleading");
  await expect(referenceFailures).not.toContainText("fetch-rate-limited");

  await runExperiment(page, "golden no-retry", "0.2.0-no-retry");
  const failures = page.getByRole("table", { name: "Failed and unresolved cases" });

  // 5. Inspect a failed trace: the rate-limited fetch that the candidate never retried.
  await failures
    .getByRole("row", { name: /fetch-rate-limited demo\.fleet-landings/ })
    .getByRole("link", { name: "Trace" })
    .click();
  const events = page.getByRole("list", { name: "Trace events" });
  await expect(
    events.getByRole("button", { name: /fault injected: rl \(rate limit\)/ }),
  ).toBeVisible();
  await events
    .getByRole("button", { name: /tool result: fetch_document error/ })
    .first()
    .click();
  await expect(page.getByLabel("Selected event").getByText("Error details")).toBeVisible();
  await expect(page.getByRole("region", { name: "Observed (facts from the trace)" })).toBeVisible();

  // 8. Submit a human review annotation for that failed run.
  await page.getByRole("link", { name: "Review this run" }).click();
  const submit = page.getByRole("form", { name: "Submit a review" });
  await submit.getByLabel("Reviewer name").fill("golden-path-reviewer");
  await submit.getByLabel("I decided this label before looking at the automated verdict").check();
  await submit.getByRole("radio", { name: "fail" }).check();
  await submit.getByRole("button", { name: "Submit review" }).click();
  await expect(submit.getByRole("status")).toContainText("saved");
  await expect(page.getByRole("heading", { name: "golden-path-reviewer" })).toBeVisible();

  // 6-7. Compare the two configurations; the seeded regression fails the policy.
  await page.goto("/compare");
  const pick = async (field: string, name: string) => {
    const select = page.getByRole("combobox", { name: new RegExp(`^${field}`) });
    const value = await select.locator("option", { hasText: name }).getAttribute("value");
    await select.selectOption(value!);
  };
  await pick("Baseline", "golden reference");
  await pick("Candidate", "golden no-retry");
  await page.getByRole("button", { name: "Compare" }).click();
  await expect(page.getByText("Regression policy failed (default policy)")).toBeVisible();
  const cases = page.getByRole("table", { name: "Case-level comparison" });
  // Rows read "<scenario> <case> <change> …"; the seeded regression breaks every retry scenario.
  const regressions = [
    ["search-timeout-once", "lighthouse-year"],
    ["fetch-rate-limited", "fleet-landings"],
    ["search-malformed-once", "fleet-landings"],
    ["fetch-temporary-error", "population-1950"],
    ["search-timeout-once", "university-founding"],
  ];
  for (const [scenario, demoCase] of regressions) {
    await expect(
      cases.getByRole("row", { name: new RegExp(`^${scenario} demo\\.${demoCase} newly failing`) }),
    ).toBeVisible();
  }
  await expect(cases.getByRole("row", { name: /newly failing/ })).toHaveCount(regressions.length);
  // The reference agent's controlled failure fails for both: unchanged, not a regression.
  await expect(cases.getByRole("row", { name: /fetch-misleading.*newly failing/ })).toHaveCount(0);
});
