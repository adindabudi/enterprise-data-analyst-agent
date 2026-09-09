import { expect, test } from "@playwright/test";

import { openAuthenticatedWorkspace } from "./fixtures";

test.beforeEach(async ({ baseURL, context }) => {
  expect(
    baseURL,
    "Mocked resume tests must only target the local harness",
  ).toBe("http://127.0.0.1:4173");
  await context.route(/^https?:\/\//, async (route) => {
    if (new URL(route.request().url()).origin !== baseURL) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.fallback();
  });
});

for (const status of ["completed", "failed", "cancelled"] as const) {
  test(`refresh restores ${status} task from idless snapshots`, async ({
    page,
  }, testInfo) => {
    await openAuthenticatedWorkspace(page, testInfo);
    let eventRequests = 0;
    let steeringRequests = 0;
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/steer")) {
        steeringRequests += 1;
      }
    });
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
    await page.route("**/api/tasks/task_e2e_12345678/events", async (route) => {
      eventRequests += 1;
      const envelope = {
        sessionId: "ses_e2e_12345678",
        taskId: "task_e2e_12345678",
        occurredAt: "2026-09-07T00:00:00Z",
        provenanceRefs: [],
      };
      const events = [
        {
          ...envelope,
          eventId: "evt_checkpoint_12345678",
          sequence: 1,
          type: "task.checkpointed",
          payload: { status, checkpointSequence: 7 },
        },
        {
          ...envelope,
          eventId: "evt_terminal_12345678",
          sequence: 2,
          type: `run.${status}`,
          payload: {
            status,
            finalMessageId:
              status === "completed" ? "msg_final_12345678" : null,
          },
        },
      ];
      await route.fulfill({
        contentType: "text/event-stream",
        body: events
          .map(
            (event) =>
              `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
          )
          .join(""),
      });
    });

    await page.goto("/?task=task_e2e_12345678");

    const conversation = page.getByRole("region", {
      name: "Analysis conversation",
    });
    await expect(
      page.getByRole("button", { name: "Stop current task" }),
    ).toBeDisabled();
    await expect(
      page.getByText("Live updates reconnecting", { exact: true }),
    ).toHaveCount(0);
    await expect(conversation.getByRole("progressbar")).toHaveCount(0);
    await expect(
      conversation.getByRole("button", { name: `Analysis ${status}` }),
    ).toBeVisible();
    const restoredAnswer = page.getByText(
      "The answer is grounded and complete.",
      { exact: true },
    );
    if (status === "completed") {
      await expect(restoredAnswer).toBeVisible();
    } else {
      await expect(restoredAnswer).toHaveCount(0);
    }
    await page.screenshot({
      path: `/tmp/eda-task-resume-${testInfo.project.name}-${status}.png`,
      fullPage: true,
    });

    await page
      .getByRole("textbox", { name: "Analysis request" })
      .fill("How many rooms are occupied?");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page).toHaveURL(/\?session=ses_e2e_12345678$/);
    await expect(restoredAnswer).toHaveCount(status === "completed" ? 2 : 1);
    expect(steeringRequests).toBe(0);
    expect(eventRequests).toBe(1);
    expect(errors).toEqual([]);
  });
}
