import { expect, test } from "@playwright/test";

import {
  expectNoBrowserSecrets,
  isMobileProject,
  openAuthenticatedWorkspace,
} from "./fixtures";

test.beforeEach(async ({ baseURL, context }) => {
  expect(baseURL).toBe("http://127.0.0.1:4173");
  await context.route(/^https?:\/\//, async (route) => {
    if (new URL(route.request().url()).origin !== baseURL)
      return route.abort("blockedbyclient");
    await route.fallback();
  });
});

test("saved sessions restore files, tasks and previews after reload", async ({
  page,
}, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  const sessionId = "ses_e2e_12345678";
  const otherSessionId = "ses_other_12345678";
  const now = "2026-09-08T00:00:00Z";
  const sessions = [
    { sessionId, title: "Hospital occupancy", lastActivityAt: now },
    {
      sessionId: otherSessionId,
      title: "Monthly revenue",
      lastActivityAt: now,
    },
  ];
  const tasks = ["task_e2e_12345678", "task_dashboard_12345678"].map(
    (taskId) => ({
      taskId,
      status: "completed",
      sourceMessageId: "msg_question_12345678",
      finalMessageId: "msg_result_12345678",
      createdAt: now,
      updatedAt: now,
    }),
  );
  const artifacts = tasks.map((task, index) => ({
    artifactId: `artifact-output${String(index)}_12345678`,
    version: 2,
    kind: index === 0 ? "xlsx" : "html",
    sha256: "a".repeat(64),
    displayName: index === 0 ? "occupancy.xlsx" : "occupancy-dashboard.html",
    sizeBytes: 8192,
    taskId: task.taskId,
  }));
  await page.route("**/api/sessions**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const selected = path.includes(otherSessionId) ? sessions[1] : sessions[0];
    if (path === "/api/sessions") return route.fulfill({ json: sessions });
    if (path.endsWith("/history"))
      return route.fulfill({
        json: {
          messages:
            selected?.sessionId === sessionId
              ? [
                  {
                    messageId: "msg_question_12345678",
                    role: "user",
                    text: "Export room occupancy",
                    createdAt: now,
                    taskId: null,
                  },
                  {
                    messageId: "msg_result_12345678",
                    role: "assistant",
                    text: "The workbook and dashboard are ready.",
                    createdAt: now,
                    taskId: tasks[1]?.taskId,
                  },
                ]
              : [],
          tasks: selected?.sessionId === sessionId ? tasks : [],
        },
      });
    if (path.endsWith("/todos"))
      return route.fulfill({
        json: {
          items:
            selected?.sessionId === sessionId
              ? [
                  {
                    id: 1,
                    title: "Read occupancy results",
                    description: null,
                    isComplete: true,
                  },
                  {
                    id: 2,
                    title: "Build requested files",
                    description: null,
                    isComplete: true,
                  },
                  {
                    id: 3,
                    title: "Validate and publish",
                    description: null,
                    isComplete: true,
                  },
                ]
              : [],
        },
      });
    return route.fulfill({ json: selected });
  });
  await page.route("**/api/tasks/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const taskId = path.split("/")[3];
    if (path.endsWith("/artifacts"))
      return route.fulfill({
        json: {
          artifacts: artifacts.filter((artifact) => artifact.taskId === taskId),
        },
      });
    if (path.endsWith("/provenance"))
      return route.fulfill({ json: { artifacts: [], sourceQueries: [] } });
    if (path.endsWith("/content"))
      return route.fulfill({
        contentType: "text/html",
        body: '<!doctype html><html><head><script type="module">document.getElementById("root").textContent="Restored HTML dashboard"</script></head><body><h1 id="root"></h1></body></html>',
      });
    if (path.includes("/messages/"))
      return route.fulfill({
        json: {
          messageId: "msg_result_12345678",
          role: "assistant",
          text: "The workbook and dashboard are ready.",
        },
      });
    if (path.endsWith("/events")) {
      const event = {
        eventId: "evt_saved_12345678",
        sequence: 1,
        sessionId,
        taskId,
        occurredAt: now,
        type: "run.completed",
        payload: { status: "completed", finalMessageId: "msg_result_12345678" },
        provenanceRefs: [],
      };
      return route.fulfill({
        contentType: "text/event-stream",
        body: `event: run.completed\ndata: ${JSON.stringify(event)}\n\n`,
      });
    }
    return route.fulfill({
      json: { ...tasks.find((task) => task.taskId === taskId), sessionId },
    });
  });

  await page.goto(`/?session=${sessionId}`);
  await expect(
    page.getByRole("heading", { name: "Hospital occupancy" }),
  ).toBeVisible();
  await expect(
    page.getByText("The workbook and dashboard are ready."),
  ).toBeVisible();
  if (isMobileProject(testInfo))
    await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(page.getByRole("button", { name: /^Tasks/ })).toContainText(
    "3 / 3",
  );
  const outputs = page.getByRole("region", { name: "Outputs", exact: true });
  await expect(
    outputs.getByRole("link", { name: "Download occupancy.xlsx" }),
  ).toHaveAttribute("href", /task_e2e_12345678\/artifacts\//);
  await expect(
    outputs.getByRole("link", { name: "Download occupancy-dashboard.html" }),
  ).toHaveAttribute("href", /task_dashboard_12345678\/artifacts\//);
  await outputs
    .getByRole("button", { name: "Preview occupancy-dashboard.html" })
    .click();
  await expect(
    page
      .frameLocator('iframe[title="Artifact preview"]')
      .getByRole("heading", { name: "Restored HTML dashboard" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close preview" }).click();
  await expect(
    page.getByRole("button", { name: "Preview occupancy-dashboard.html" }),
  ).toBeFocused();
  await page.screenshot({
    path: `/tmp/eda-session-panel-${testInfo.project.name}.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  if (isMobileProject(testInfo)) {
    await page.getByRole("button", { name: "Close workspace" }).click();
    await page.getByRole("button", { name: "Analyses" }).click();
  }
  await page
    .getByRole("button", { name: "Monthly revenue", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Monthly revenue" }),
  ).toBeVisible();
  if (isMobileProject(testInfo))
    await page.getByRole("button", { name: "Analyses" }).click();
  await page
    .getByRole("button", { name: "Hospital occupancy", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Hospital occupancy" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("The workbook and dashboard are ready."),
  ).toBeVisible();
  if (isMobileProject(testInfo))
    await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page.getByRole("link", { name: "Download occupancy.xlsx" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop current task" }),
  ).toBeDisabled();
  await expectNoBrowserSecrets(page);
});
