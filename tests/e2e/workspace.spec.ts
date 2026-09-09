import { expect, test } from "@playwright/test";

import {
  analysisEventStream,
  expectNoBrowserSecrets,
  isMobileProject,
  openAuthenticatedWorkspace,
} from "./fixtures";

test("private chat streams progress and commits the canonical response", async ({
  page,
}, testInfo) => {
  let durableTaskRequests = 0;
  page.on("request", (request) => {
    if (
      /\/api\/sessions\/[^/]+\/tasks$/.test(new URL(request.url()).pathname)
    ) {
      durableTaskRequests += 1;
    }
  });
  await openAuthenticatedWorkspace(page, testInfo);

  if (isMobileProject(testInfo)) {
    await page.getByRole("button", { name: "Analyses" }).click();
    await expect(
      page.getByRole("dialog", { name: "Analyses drawer" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "New chat" }).last().click();
    await expect(
      page.getByRole("heading", { name: "New private analysis" }),
    ).toBeVisible();
  }

  await page.getByLabel("Analysis request").fill("What is two plus two?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page
      .getByRole("region", { name: "Analysis conversation" })
      .getByText("What is two plus two?"),
  ).toBeVisible();
  await expect(
    page.getByText("The answer is grounded and complete."),
  ).toBeVisible();
  expect(durableTaskRequests).toBe(0);
  await expectNoBrowserSecrets(page);
});

test("new chat clears local context and returns focus to the composer", async ({
  page,
}, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  const composer = page.getByLabel("Analysis request");
  await composer.fill("Draft that should not cross threads");
  await page.getByRole("button", { name: "New chat" }).first().click();

  await expect(
    page.getByRole("heading", { name: "New private analysis" }),
  ).toBeVisible();
  await expect(page.getByLabel("Analysis request")).toHaveValue("");
  await expect(page.getByLabel("Analysis request")).toBeFocused();
  await expect(page).toHaveURL(/\/$/);
});

test("cancellation is queued while the agent is running", async ({
  page,
}, testInfo) => {
  test.skip(
    isMobileProject(testInfo),
    "desktop task controls are covered in the desktop shell",
  );
  await openAuthenticatedWorkspace(page, testInfo);
  await page.route("**/api/tasks/task_e2e_12345678/events", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: analysisEventStream({ terminal: false }),
    });
  });

  await page.getByRole("button", { name: "Add context" }).click();
  await page.getByRole("menuitem", { name: "Deep analysis" }).click();
  await page.getByLabel("Analysis request").fill("Run a long analysis");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.getByRole("button", { name: "Stop current task" }).click();
  await expect(
    page.locator("#workspace-main").getByText("Cancelling analysis"),
  ).toBeVisible();
});

for (const status of ["completed", "failed", "cancelled"] as const) {
  test(`XLSX-only handoff stops loading when the run is ${status}`, async ({
    page,
    baseURL,
  }, testInfo) => {
    expect(
      baseURL,
      "Mocked export tests must only target the local harness",
    ).toBe("http://127.0.0.1:4173");
    await page.context().route(/^https?:\/\//, async (route) => {
      if (new URL(route.request().url()).origin !== baseURL) {
        await route.abort("blockedbyclient");
        return;
      }
      await route.fallback();
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await openAuthenticatedWorkspace(page, testInfo);
    const question = "Export room occupancy to XLSX only.";
    const step = {
      stepId: "step-occupancy",
      kind: "gql",
      label: "Read room occupancy",
      state: "completed",
      query: "MATCH (room:rooms) RETURN room.RoomType, count(room)",
      source: "lamna-healthcare",
      querySha256: "a".repeat(64),
      rowCount: "5",
    };
    await page.route("**/api/sessions/ses_e2e_12345678/chat", async (route) => {
      await route.fulfill({
        contentType: "text/event-stream",
        body:
          `event: data_step\ndata: ${JSON.stringify(step)}\n\n` +
          'event: analysis_started\ndata: {"taskId":"task_e2e_12345678"}\n\n',
      });
    });
    await page.route(
      "**/api/tasks/task_e2e_12345678/artifacts",
      async (route) => {
        await route.fulfill({
          json: {
            artifacts: [
              {
                artifactId: "artifact-workbook_12345678",
                version: 2,
                kind: "xlsx",
                sha256: "b".repeat(64),
                displayName: "occupancy.xlsx",
                sizeBytes: 8192,
              },
            ],
          },
        });
      },
    );
    await page.route(
      "**/api/tasks/task_e2e_12345678/provenance",
      async (route) => {
        await route.fulfill({ json: { sourceQueries: [], artifacts: [] } });
      },
    );
    let finishRun: () => void = () => undefined;
    const terminalReady = new Promise<void>((resolve) => {
      finishRun = resolve;
    });
    await page.route("**/api/tasks/task_e2e_12345678/events", async (route) => {
      await terminalReady;
      const event = {
        eventId: "evt_terminal_12345678",
        sequence: 3,
        sessionId: "ses_e2e_12345678",
        taskId: "task_e2e_12345678",
        occurredAt: "2026-09-07T00:00:00Z",
        type: `run.${status}`,
        payload: {
          status,
          finalMessageId: status === "completed" ? "msg_final_12345678" : null,
        },
        provenanceRefs: [],
      };
      await route.fulfill({
        contentType: "text/event-stream",
        body: `id: 3-0\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
      });
    });
    await page.getByLabel("Analysis request").fill(question);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page).toHaveURL(
      /\?session=ses_e2e_12345678&task=task_e2e_12345678$/,
    );
    const stop = page.getByRole("button", { name: "Stop current task" });
    const conversation = page.getByRole("region", {
      name: "Analysis conversation",
    });
    await expect(stop).toBeEnabled();
    await expect(
      conversation.getByRole("progressbar", { name: "Reading the source" }),
    ).toHaveCount(0);
    if (!isMobileProject(testInfo)) {
      const dataSteps = conversation.getByRole("region", {
        name: "Data steps",
      });
      await expect(dataSteps.locator("xpath=ancestor::article")).toContainText(
        question,
      );
      await dataSteps.getByRole("button", { name: /Analyzed/ }).click();
      await dataSteps.getByRole("button", { name: step.label }).click();
      await expect(dataSteps.getByText(step.query)).toBeVisible();
    }

    finishRun();

    await expect(stop).toBeDisabled();
    await expect(conversation.getByRole("progressbar")).toHaveCount(0);
    if (status === "completed") {
      await expect(
        page.getByText("The answer is grounded and complete."),
      ).toBeVisible();
      if (!isMobileProject(testInfo)) {
        const outputs = page.getByRole("region", {
          name: "Outputs",
          exact: true,
        });
        await expect(outputs.getByText("occupancy.xlsx")).toBeVisible();
        await expect(
          outputs.getByRole("link", {
            name: "Download occupancy.xlsx",
            exact: true,
          }),
        ).toHaveCount(1);
      }
    }
    if (!isMobileProject(testInfo)) {
      await expect(conversation.getByText(step.query)).toBeVisible();
    }
    expect(errors).toEqual([]);
    await page.screenshot({
      path: `/tmp/eda-xlsx-${testInfo.project.name}-${status}.png`,
      fullPage: true,
    });
  });
}
