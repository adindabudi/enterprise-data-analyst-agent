import { expect, type Page, type TestInfo } from "@playwright/test";

export async function mockAuthenticated(page: Page): Promise<void> {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "authenticated",
        expiresAt: "2026-07-26T12:00:00Z",
      }),
    });
  });
}

export async function mockAnalysisApi(page: Page): Promise<void> {
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        sessionId: "ses_e2e_12345678",
        title: "Private analysis",
      }),
    });
  });
  await page.route("**/api/sessions/ses_e2e_12345678", async (route) => {
    await route.fulfill({
      json: {
        sessionId: "ses_e2e_12345678",
        title: "Private analysis",
        lastActivityAt: "2026-09-08T00:00:00Z",
      },
    });
  });
  await page.route(
    "**/api/sessions/ses_e2e_12345678/history",
    async (route) => {
      const taskId = new URL(page.url()).searchParams.get("task");
      await route.fulfill({
        json: {
          messages: [],
          tasks: taskId
            ? [
                {
                  taskId,
                  status: "analyzing",
                  sourceMessageId: null,
                  finalMessageId: null,
                  createdAt: "2026-09-08T00:00:00Z",
                  updatedAt: "2026-09-08T00:00:00Z",
                },
              ]
            : [],
        },
      });
    },
  );
  await page.route("**/api/sessions/ses_e2e_12345678/todos", async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route("**/api/tasks/task_e2e_12345678", async (route) => {
    await route.fulfill({
      json: {
        taskId: "task_e2e_12345678",
        sessionId: "ses_e2e_12345678",
        status: "analyzing",
        finalMessageId: null,
      },
    });
  });
  await page.route(
    "**/api/sessions/ses_e2e_12345678/messages",
    async (route) => {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ messageId: "msg_e2e_12345678" }),
      });
    },
  );
  await page.route("**/api/sessions/ses_e2e_12345678/tasks", async (route) => {
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        taskId: "task_e2e_12345678",
        sessionId: "ses_e2e_12345678",
        status: "planning",
        checkpointSequence: 0,
        activeAttemptId: null,
        finalMessageId: null,
      }),
    });
  });
  await page.route("**/api/tasks/task_e2e_12345678/cancel", async (route) => {
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ commandId: "cmd_cancel_12345678", sequence: 1 }),
    });
  });
  await page.route(
    "**/api/tasks/task_e2e_12345678/messages/msg_final_12345678",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          messageId: "msg_final_12345678",
          role: "assistant",
          text: "The answer is grounded and complete.",
        }),
      });
    },
  );
  await page.route("**/api/tasks/task_e2e_12345678/events", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: analysisEventStream(),
    });
  });
}

export function analysisEventStream(
  options: { terminal?: boolean } = {},
): string {
  const terminal = options.terminal ?? true;
  const events: Array<{ id: string; type: string; data: object }> = [
    {
      id: "1-0",
      type: "analysis_progress",
      data: activityEvent("evt_thinking_12345678", 1, "analysis_progress", {
        milestone: "Agent is thinking",
        detail: "Preparing tools and response.",
      }),
    },
    {
      id: "2-0",
      type: "message.delta",
      data: {
        ...activityEvent("evt_delta_12345678", 2, "message.delta", {
          delta: "Working on it.",
        }),
        responseAttemptId: "attempt_12345678",
        attemptSequence: 0,
      },
    },
  ];
  if (terminal) {
    events.push({
      id: "3-0",
      type: "run.completed",
      data: activityEvent("evt_complete_12345678", 3, "run.completed", {
        status: "completed",
        finalMessageId: "msg_final_12345678",
      }),
    });
  }
  return events
    .map(
      (event) =>
        `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`,
    )
    .join("");
}

function activityEvent(
  eventId: string,
  sequence: number,
  type: string,
  payload: object,
): object {
  return {
    eventId,
    sequence,
    sessionId: "ses_e2e_12345678",
    taskId: "task_e2e_12345678",
    occurredAt: "2026-07-28T00:00:00Z",
    type,
    payload,
    provenanceRefs: [],
  };
}

export async function openAuthenticatedWorkspace(
  page: Page,
  testInfo?: TestInfo,
  query = "",
): Promise<void> {
  await page.context().addCookies([
    {
      name: "eda_csrf",
      value: "csrf-e2e-value",
      url: "http://127.0.0.1:4173",
      sameSite: "Strict",
    },
  ]);
  await mockAuthenticated(page);
  await mockAnalysisApi(page);
  await page.goto(`/${query}`);
  await expect(
    page.getByRole("heading", { name: "Enterprise Data Analyst" }),
  ).toBeVisible();
  if (testInfo?.project.name.includes("mobile")) {
    await expect(page.getByRole("button", { name: "Analyses" })).toBeVisible();
  } else {
    await expect(
      page.getByRole("navigation", { name: "Analyses" }),
    ).toBeVisible();
  }
}

export async function expectNoBrowserSecrets(page: Page): Promise<void> {
  const storage = await page.evaluate(() => {
    const values = (source: Storage): Record<string, string | null> =>
      Object.fromEntries(
        Array.from({ length: source.length }, (_, index) => {
          const key = source.key(index) ?? "";
          return [key, source.getItem(key)];
        }),
      );
    return { local: values(localStorage), session: values(sessionStorage) };
  });
  expect(JSON.stringify(storage).toLowerCase()).not.toContain("token");
  expect(JSON.stringify(storage)).not.toContain("1,250,000.50");
}

export function isMobileProject(testInfo: TestInfo): boolean {
  return testInfo.project.name.includes("mobile");
}
