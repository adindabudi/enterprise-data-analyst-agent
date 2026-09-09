import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { expectNoBrowserSecrets, mockAuthenticated } from "./fixtures";

const TASK_ID = "task_fabric1234";
const MICROSOFT_LOGIN =
  "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/oauth2/v2.0/authorize";

test("Fabric consent starts only after an explicit accessible action", async ({
  page,
}) => {
  await mockAuthenticated(page);
  await page.context().addCookies([
    {
      name: "eda_csrf",
      value: "e2e-csrf",
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route(`**/api/tasks/${TASK_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body:
        "id: evt-auth-1\nevent: auth.required\n" +
        'data: {"actionPath":"/api/fabric/auth/start"}\n\n',
    });
  });
  let starts = 0;
  await page.route("**/api/fabric/auth/start", async (route) => {
    starts += 1;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBe("e2e-csrf");
    expect(route.request().postDataJSON()).toEqual({ taskId: TASK_ID });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ authorizationUrl: MICROSOFT_LOGIN }),
    });
  });
  await page.route(`${MICROSOFT_LOGIN}**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Microsoft sign in</title>",
    });
  });

  await page.goto(`/?task=${TASK_ID}`);
  const connect = page.getByRole("button", { name: "Connect to Fabric" });
  await expect(connect).toBeVisible();
  expect(starts).toBe(0);
  await expect(page).toHaveURL(`http://127.0.0.1:4173/?task=${TASK_ID}`);
  await expect(page.getByText(/login\.microsoftonline\.com/i)).toHaveCount(0);
  await expect(page.getByText(/receipt_/i)).toHaveCount(0);

  const accessibility = await new AxeBuilder({ page })
    .exclude("[data-tabster-dummy]")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    accessibility.violations.filter(
      (violation) =>
        violation.impact === "serious" || violation.impact === "critical",
    ),
  ).toEqual([]);

  await connect.click();
  await expect(page).toHaveURL(MICROSOFT_LOGIN);
  expect(starts).toBe(1);
});

test("callback completion posts once and returns to the owning task", async ({
  page,
}) => {
  await mockAuthenticated(page);
  await page.context().addCookies([
    {
      name: "eda_csrf",
      value: "e2e-csrf",
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.addInitScript((taskId) => {
    sessionStorage.setItem("eda.fabric.task", taskId);
  }, TASK_ID);
  let completions = 0;
  await page.route("**/api/fabric/auth/complete", async (route) => {
    completions += 1;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBe("e2e-csrf");
    expect(route.request().postData()).toBeNull();
    await route.fulfill({ status: 204, body: "" });
  });
  await page.route(`**/api/tasks/${TASK_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    });
  });

  await page.goto("/fabric-auth/complete");

  await expect(page).toHaveURL(`http://127.0.0.1:4173/?task=${TASK_ID}`);
  expect(completions).toBe(1);
  await expect(
    page.getByRole("heading", { name: "Enterprise Data Analyst" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => sessionStorage.getItem("eda.fabric.task")),
  ).toBeNull();
  await expectNoBrowserSecrets(page);
});
