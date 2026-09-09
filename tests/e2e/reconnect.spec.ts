import { expect, test } from "@playwright/test";

import { isMobileProject, mockAuthenticated } from "./fixtures";

test("native SSE reconnect carries Last-Event-ID and deduplicates terminal state", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "desktop stream surface only");
  await mockAuthenticated(page);
  const taskId = `task_e2e_reconnect_${String(testInfo.retry)}`;

  await page.goto(`/?task=${taskId}`);

  await expect(
    page.getByRole("button", { name: "Inspecting workbook" }),
  ).toBeVisible();
  await expect(
    page.getByText("Final response committed from canonical task state."),
  ).toHaveCount(1);
  await expect(page.getByText("Reconnect cursor missing")).toHaveCount(0);
  await page.getByRole("tab", { name: "Outputs" }).click();
  await expect(page.getByText("report.html")).toBeVisible();
});

test("repeated transport failure exposes an explicit stream gap", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "desktop stream surface only");
  await mockAuthenticated(page);
  await page.route("**/api/tasks/task_12345678/events", async (route) => {
    await route.abort("connectionfailed");
  });

  await page.goto("/?task=task_12345678");

  await expect(
    page.getByText("Stream gap; using canonical state"),
  ).toBeVisible();
});
