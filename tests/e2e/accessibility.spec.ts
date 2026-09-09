import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  isMobileProject,
  mockAuthenticated,
  openAuthenticatedWorkspace,
} from "./fixtures";

async function expectNoSeriousViolations(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page })
    // Fluent 9.74 hardcodes Tabster autoRoot focus sentinels as aria-hidden,
    // focusable nodes. They are framework keyboard plumbing, not app content.
    .exclude("[data-tabster-dummy]")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const serious = result.violations.filter(
    (violation) =>
      violation.impact === "serious" || violation.impact === "critical",
  );
  expect(serious).toEqual([]);
}

test("signed-out and authenticated states have no serious axe violations", async ({
  page,
}, testInfo) => {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: "{}",
    });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
  await expectNoSeriousViolations(page);

  await page.unroute("**/api/auth/session");
  await mockAuthenticated(page);
  await page.reload();
  if (isMobileProject(testInfo)) {
    await page.getByRole("button", { name: "Analyses" }).click();
    await expect(
      page.getByRole("dialog", { name: "Analyses drawer" }),
    ).toBeVisible();
  } else {
    await expect(
      page.getByRole("navigation", { name: "Analyses" }),
    ).toBeVisible();
  }
  await expectNoSeriousViolations(page);
});

test("keyboard skip link reaches the one main workspace region", async ({
  page,
}, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);

  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.locator("#workspace-main")).toBeFocused();
  await expect(page.getByRole("main")).toHaveCount(1);
});
