import { expect, test } from "@playwright/test";

import type { FabricSourceMetadata } from "../../apps/web/src/api/fabric";
import {
  expectNoBrowserSecrets,
  isMobileProject,
  openAuthenticatedWorkspace,
} from "./fixtures";

test.beforeEach(async ({ baseURL, context }) => {
  expect(
    baseURL,
    "Mocked source and progress tests must only target the local harness",
  ).toBe("http://127.0.0.1:4173");
  await context.route(/^https?:\/\//, async (route) => {
    if (new URL(route.request().url()).origin !== baseURL) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.fallback();
  });
});

test("configured ontology metadata drives new titles and Inputs without changing saved titles", async ({
  page,
}, testInfo) => {
  let source: FabricSourceMetadata | undefined;
  await page.route("**/health/ready", async (route) => {
    await route.fulfill({
      json: { status: "ready", featurePacks: { fabric: "configured" } },
    });
  });
  await page.route("**/api/fabric/auth/status", async (route) => {
    await route.fulfill({
      json: { provider: "ontology", state: "linked", chatQuery: true, source },
    });
  });
  await openAuthenticatedWorkspace(page, testInfo);

  for (const metadata of [
    { alias: "logistics", description: "Logistics shipment ontology" },
    {
      alias: "energy",
      description: "Regional energy consumption and distribution ontology",
    },
  ]) {
    source = metadata;
    await page.reload();
    await expect(
      page.getByRole("heading", { name: metadata.description, level: 2 }),
    ).toBeVisible();
    await expect(page.getByText(/Lamna|healthcare|hospitals/)).toHaveCount(0);
    if (isMobileProject(testInfo))
      await page.getByRole("button", { name: "Open workspace" }).click();
    const inputs = page.getByRole("region", { name: "Inputs", exact: true });
    await expect(
      inputs.getByText(metadata.description, { exact: true }),
    ).toBeVisible();
    await expect(
      inputs.getByText(
        "Answers chat questions; deep analysis awaits acceptance",
      ),
    ).toBeVisible();
    await page.screenshot({
      path: `/tmp/eda-source-${testInfo.project.name}-${metadata.alias}.png`,
      animations: "disabled",
    });
    if (isMobileProject(testInfo))
      await page.getByRole("button", { name: "Close workspace" }).click();
    const dimensions = await page.evaluate(() => ({
      width: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  }

  await page.goto("/?session=ses_e2e_12345678");
  await expect(
    page.getByRole("heading", { name: "Private analysis", level: 2 }),
  ).toBeVisible();
  source = { alias: "logistics", description: "Logistics shipment ontology" };
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Private analysis", level: 2 }),
  ).toBeVisible();
  if (isMobileProject(testInfo))
    await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page
      .getByRole("region", { name: "Inputs", exact: true })
      .getByText(source.description, { exact: true }),
  ).toBeVisible();

  source = undefined;
  await page.goto("/");
  if (isMobileProject(testInfo))
    await page.getByRole("button", { name: "Open workspace" }).click();
  await expect(
    page
      .getByRole("region", { name: "Inputs", exact: true })
      .getByText("Fabric ontology", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Logistics shipment ontology", { exact: true }),
  ).toHaveCount(0);
  await expectNoBrowserSecrets(page);
});

test("completed publication keeps spinning until the analysis completes", async ({
  page,
}, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  await page.route(
    "**/api/tasks/task_e2e_12345678/artifacts",
    async (route) => {
      await route.fulfill({ json: { artifacts: [] } });
    },
  );
  await page.route(
    "**/api/tasks/task_e2e_12345678/provenance",
    async (route) => {
      await route.fulfill({ json: { artifacts: [], sourceQueries: [] } });
    },
  );
  let eventRequests = 0;
  let finishTask: () => void = () => undefined;
  const terminalReady = new Promise<void>((resolve) => {
    finishTask = resolve;
  });
  const envelope = {
    sessionId: "ses_e2e_12345678",
    taskId: "task_e2e_12345678",
    occurredAt: "2026-09-09T00:00:00Z",
    provenanceRefs: [],
  };
  await page.route("**/api/tasks/task_e2e_12345678/events", async (route) => {
    eventRequests += 1;
    if (eventRequests === 1) {
      const progress = {
        ...envelope,
        eventId: "evt_publication_12345678",
        sequence: 1,
        type: "analysis_progress",
        payload: {
          milestone: "Publishing output",
          state: "completed",
          detail: "Finished publish_artifact.",
        },
      };
      await route.fulfill({
        contentType: "text/event-stream",
        body: `retry: 50\nid: 1-0\nevent: analysis_progress\ndata: ${JSON.stringify(progress)}\n\n`,
      });
      return;
    }
    await terminalReady;
    const terminal = {
      ...envelope,
      eventId: "evt_terminal_12345678",
      sequence: 2,
      type: "run.completed",
      payload: { status: "completed", finalMessageId: "msg_final_12345678" },
    };
    await route.fulfill({
      contentType: "text/event-stream",
      body: `id: 2-0\nevent: run.completed\ndata: ${JSON.stringify(terminal)}\n\n`,
    });
  });

  await page.goto("/?task=task_e2e_12345678");
  const conversation = page.getByRole("region", {
    name: "Analysis conversation",
  });
  const stop = page.getByRole("button", { name: "Stop current task" });
  await expect(
    conversation.getByRole("button", { name: "Publishing output" }),
  ).toBeVisible();
  await expect(
    conversation.getByRole("progressbar", { name: "Query in progress" }),
  ).toBeVisible();
  await expect(
    conversation.getByText("running", { exact: true }),
  ).toBeVisible();
  await expect(
    conversation.getByText("completed", { exact: true }),
  ).toHaveCount(0);
  await expect(stop).toBeEnabled();
  await conversation.getByRole("button", { name: "Publishing output" }).click();
  await expect(
    conversation.getByText("Finished publish_artifact."),
  ).toBeVisible();
  await page.screenshot({
    path: `/tmp/eda-publishing-running-${testInfo.project.name}.png`,
    animations: "disabled",
  });

  finishTask();
  await expect(stop).toBeDisabled();
  await expect(conversation.getByRole("progressbar")).toHaveCount(0);
  await expect(
    conversation.getByText("completed", { exact: true }),
  ).toBeVisible();
  await expect(
    conversation.getByText("The answer is grounded and complete."),
  ).toBeVisible();
  await expectNoBrowserSecrets(page);
});
