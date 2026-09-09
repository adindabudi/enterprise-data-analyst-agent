import { expect, test } from "@playwright/test";

import { expectNoBrowserSecrets, mockAuthenticated } from "./fixtures";

test("pending auth always leaves a visible nonblank status", async ({
  page,
}) => {
  await page.route("**/api/auth/session", async () => {
    await new Promise<never>(() => undefined);
  });

  await page.goto("/");

  await expect(
    page.getByRole("progressbar", { name: "Preparing your session" }),
  ).toBeVisible();
  await expect(page.locator("#root")).not.toBeEmpty();
  await expectNoBrowserSecrets(page);
});

test("401 stays signed out until the user chooses sign in", async ({
  page,
}) => {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: "{}",
    });
  });

  await page.goto("/");

  await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
  await expect(page).toHaveURL("http://127.0.0.1:4173/");
  await expectNoBrowserSecrets(page);
});

test("malformed bootstrap data renders Retry and can recover", async ({
  page,
}, testInfo) => {
  let healthy = false;
  await page.route("**/api/auth/session", async (route) => {
    if (healthy) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "authenticated" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "{",
    });
  });

  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Workspace could not connect",
  );
  healthy = true;
  await page.getByRole("button", { name: "Retry" }).click();

  if (testInfo.project.name.includes("mobile")) {
    await expect(page.getByRole("button", { name: "Analyses" })).toBeVisible();
  } else {
    await expect(
      page.getByRole("navigation", { name: "Analyses" }),
    ).toBeVisible();
  }
});

test("network rejection renders an accessible Retry fallback", async ({
  page,
}) => {
  await page.route("**/api/auth/session", async (route) => {
    await route.abort("connectionfailed");
  });

  await page.goto("/");

  await expect(page.getByRole("alert")).toContainText(
    "Workspace could not connect",
  );
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator("#root")).not.toBeEmpty();
});

test("lazy application chunk rejection renders the root fallback", async ({
  page,
}) => {
  await mockAuthenticated(page);
  await page.route(/\/assets\/App-.*\.js$/, async (route) => {
    await route.abort("failed");
  });

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Workspace unavailable" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
  await expect(page.locator("#root")).not.toBeEmpty();
});
