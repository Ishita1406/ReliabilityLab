import { expect, test } from "@playwright/test";

// Background execution end to end: the UI creates an experiment, the API returns at once
// (202), a real Celery worker runs it through Redis, and the page polls until it completes.

test("create an experiment in the UI and watch it run in the background", async ({ page }) => {
  await page.goto("/experiments");
  const form = page.getByRole("form", { name: "New experiment" });
  await form.getByLabel("Name").fill("e2e background run");
  const suite = form.getByLabel("Suite");
  await suite.selectOption(
    (await suite.locator("option", { hasText: "Evaluation samples" }).getAttribute("value"))!,
  );
  const agent = form.getByLabel("Agent version");
  await agent.selectOption(
    (await agent
      .locator("option", { hasText: "0.1.0 (deterministic/regex-extractor)" })
      .first()
      .getAttribute("value"))!,
  );
  await form.getByRole("button", { name: "Create and queue" }).click();

  // Redirected to the detail page immediately, before the benchmark has finished.
  await expect(page).toHaveURL(/\/experiments\/exp_[0-9a-f]{32}$/);
  await expect(page.getByRole("heading", { name: "e2e background run" })).toBeVisible();

  // Polling updates the page without a manual reload until the worker finishes.
  await expect(page.getByText("12 of 12 runs stored · 12 evaluated")).toBeVisible({
    timeout: 60_000,
  });
  const status = page.getByRole("region", { name: "Status" });
  await expect(status.getByText("completed", { exact: true })).toBeVisible();
  await expect(status.getByText(/refreshing every/)).toHaveCount(0); // polling stopped
  await expect(page.getByRole("table", { name: "Failed and unresolved cases" })).toContainText(
    "fetch-misleading",
  );

  // The list shows it as completed too.
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Experiments" })
    .click();
  await expect(page.getByRole("row", { name: /e2e background run.*completed/ })).toBeVisible();
});

test("a pending experiment can be cancelled from its page", async ({ page }) => {
  await page.goto("/experiments");
  await page.getByRole("link", { name: "[demo] pending (0.1.0)" }).click();
  const status = page.getByRole("region", { name: "Status" });
  await expect(status.getByText("pending", { exact: true })).toBeVisible();
  await status.getByRole("button", { name: "Cancel" }).click();
  await expect(status.getByText("cancelled", { exact: true })).toBeVisible();
  await expect(status.getByRole("button")).toHaveCount(0);
});
