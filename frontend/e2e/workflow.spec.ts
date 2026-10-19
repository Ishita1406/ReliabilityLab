import { expect, test } from "@playwright/test";

// Runs against the seeded demo data (see e2e/global-setup.ts). Navigation goes through the UI
// only, so these tests exercise real links, server rendering, and the API together.

test("review workflow: experiment → failed case → trace → review → correction", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Latest completed experiment" })).toBeVisible();

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Experiments" })
    .click();
  await page.getByRole("link", { name: "[demo] baseline (0.1.0)" }).click();
  await expect(page.getByText("12 of 12 planned runs stored · 12 evaluated")).toBeVisible();

  const failed = page.getByRole("table", { name: "Failed and unresolved cases" });
  const row = failed.getByRole("row", { name: /fetch-misleading/ });
  await row.getByRole("link", { name: "Trace" }).click();

  // Trace explorer: select a tool call and see its input.
  const events = page.getByRole("list", { name: "Trace events" });
  await events
    .getByRole("button", { name: /tool call: fetch_document/ })
    .first()
    .click();
  const detail = page.getByLabel("Selected event");
  await expect(detail.getByText("Tool input (arguments)")).toBeVisible();
  await expect(page.getByRole("region", { name: "Observed (facts from the trace)" })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Inferred candidate root causes (hypotheses, not proven)" }),
  ).toBeVisible();

  // Review the run.
  await page.getByRole("link", { name: "Review this run" }).click();
  await expect(page.getByRole("heading", { name: "Evaluator judgment" })).toBeVisible();
  await expect(page.getByText("No human reviews yet")).toBeVisible();
  const submit = page.getByRole("form", { name: "Submit a review" });
  await submit.getByLabel("Reviewer name").fill("e2e-reviewer");
  await submit.getByLabel("I decided this label before looking at the automated verdict").check();
  await submit.getByRole("radio", { name: "fail" }).check();
  await submit.getByRole("button", { name: "Submit review" }).click();
  await expect(submit.getByRole("status")).toContainText("saved");
  await expect(page.getByRole("heading", { name: "e2e-reviewer" })).toBeVisible();

  // Correct it: the original stays in the history.
  await page.getByText("Correct this review").click();
  const correct = page.getByRole("form", { name: "Correct e2e-reviewer's review" });
  await correct.getByLabel("Reason for the correction").fill("the conflict was reported correctly");
  await correct.getByRole("radio", { name: "ambiguous" }).check();
  await correct.getByRole("button", { name: "Save correction" }).click();
  await expect(correct.getByRole("alert")).toContainText("fix the highlighted fields");
  await correct
    .getByLabel(/Rationale/)
    .fill("the expected outcome for conflicting sources is debatable");
  await correct.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByText("History (2 records)")).toBeVisible();
  await page.getByText("History (2 records)").click();
  await expect(
    page.getByText("Correction: the conflict was reported correctly").first(),
  ).toBeVisible();
});

test("comparison shows case-level regressions and the failed policy", async ({ page }) => {
  await page.goto("/compare");
  const pick = async (field: string, name: string) => {
    // The <select> sits inside its <label>, whose text also contains every option, so match the
    // combobox by role and accessible-name prefix instead of by label text.
    const select = page.getByRole("combobox", { name: new RegExp(`^${field}`) });
    const value = await select.locator("option", { hasText: name }).getAttribute("value");
    await select.selectOption(value!);
  };
  await pick("Baseline", "[demo] baseline");
  await pick("Candidate", "[demo] no-retry");
  await page.getByRole("button", { name: "Compare" }).click();

  await expect(page.getByText("Regression policy failed (default policy)")).toBeVisible();
  const cases = page.getByRole("table", { name: "Case-level comparison" });
  await expect(
    cases.getByRole("row", { name: /search-timeout-once.*newly failing/ }),
  ).toBeVisible();
  await expect(
    cases.getByRole("row", { name: /search-malformed-once.*newly failing/ }),
  ).toBeVisible();
});

test("benchmark explorer filters cases", async ({ page }) => {
  await page.goto("/benchmarks");
  await page.getByRole("link", { name: "Research Agent Sample Suite" }).click();
  await expect(page.getByRole("status")).toContainText("Showing 5 of 5 cases");
  await page.getByLabel("Expected outcome").selectOption("insufficient_information");
  await expect(page.getByRole("status")).toContainText("Showing 1 of 5 cases");
});

test("unknown experiment renders an error state, not an empty page", async ({ page }) => {
  await page.goto(`/experiments/exp_${"0".repeat(32)}`);
  // Next.js also renders an (empty) role="alert" route announcer.
  await expect(
    page.getByRole("alert").filter({ hasText: "Could not load the experiment" }),
  ).toBeVisible();
});
