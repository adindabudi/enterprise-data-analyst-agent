import { afterEach, expect, it, vi } from "vitest";

import {
  listAnalysisSessions,
  readAnalysisHistory,
  readAnalysisSession,
  readAnalysisTask,
} from "./analysis";

afterEach(() => vi.unstubAllGlobals());

it("loads canonical sessions, history and task identities with same-origin credentials", async () => {
  const session = {
    sessionId: "ses_history_12345678",
    title: "Room occupancy",
    lastActivityAt: "2026-09-08T00:00:00Z",
  };
  const history = {
    messages: [
      {
        messageId: "msg_history_12345678",
        role: "user",
        text: "Export results",
        createdAt: session.lastActivityAt,
        taskId: null,
      },
    ],
    tasks: [
      {
        taskId: "task_history_12345678",
        status: "completed",
        sourceMessageId: "msg_history_12345678",
        finalMessageId: null,
        createdAt: session.lastActivityAt,
        updatedAt: session.lastActivityAt,
      },
    ],
  };
  const task = {
    taskId: "task_history_12345678",
    sessionId: session.sessionId,
    status: "completed",
    finalMessageId: null,
  };
  const responses = [[session], session, history, task];
  const fetchMock = vi.fn<typeof fetch>(() =>
    Promise.resolve(
      new Response(JSON.stringify(responses.shift()), {
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  expect(await listAnalysisSessions()).toEqual([session]);
  expect(await readAnalysisSession(session.sessionId)).toEqual(session);
  expect(await readAnalysisHistory(session.sessionId)).toEqual(history);
  expect(await readAnalysisTask(task.taskId)).toEqual(task);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/sessions",
    `/api/sessions/${session.sessionId}`,
    `/api/sessions/${session.sessionId}/history`,
    `/api/tasks/${task.taskId}`,
  ]);
  for (const [, options] of fetchMock.mock.calls)
    expect(options?.credentials).toBe("same-origin");
});

it("rejects malformed history instead of silently showing an empty session", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(JSON.stringify({ messages: [{}], tasks: [] })),
      ),
    ),
  );
  await expect(readAnalysisHistory("ses_history_12345678")).rejects.toThrow();
});
