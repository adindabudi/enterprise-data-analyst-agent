import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useSessionHistory } from "./useSessionHistory";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const session = {
  sessionId: "ses_history_12345678",
  title: "Saved occupancy",
  lastActivityAt: "2026-09-08T00:00:00Z",
};
const history = {
  messages: [
    {
      messageId: "msg_history_12345678",
      role: "assistant",
      text: "Workbook ready",
      taskId: "task_history_12345678",
      createdAt: session.lastActivityAt,
    },
  ],
  tasks: [
    {
      taskId: "task_history_12345678",
      status: "completed",
      sourceMessageId: null,
      finalMessageId: "msg_history_12345678",
      createdAt: session.lastActivityAt,
      updatedAt: session.lastActivityAt,
    },
  ],
};

it("restores a task URL through its durable session and lists saved analyses", async () => {
  window.history.replaceState(null, "", "/?task=task_history_12345678");
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = input instanceof Request ? input.url : input.toString();
      const value =
        path === "/api/sessions"
          ? [session]
          : path.endsWith("/history")
            ? history
            : path.startsWith("/api/tasks/")
              ? { ...history.tasks[0], sessionId: session.sessionId }
              : session;
      return Promise.resolve(new Response(JSON.stringify(value)));
    }),
  );
  const { result } = renderHook(() => useSessionHistory());

  expect(result.current.loading).toBe(true);
  await waitFor(() => {
    expect(result.current.restored?.history.messages[0]?.text).toBe(
      "Workbook ready",
    );
  });
  expect(result.current.restored?.session.title).toBe("Saved occupancy");
  expect(result.current.sessions).toEqual([session]);
  expect(result.current.restored?.taskId).toBe("task_history_12345678");
});

it("does not restore a late response after opening a new chat", async () => {
  window.history.replaceState(null, "", `/?session=${session.sessionId}`);
  let finish: (response: Response) => void = () => undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = input instanceof Request ? input.url : input.toString();
      if (path.endsWith("/history"))
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return Promise.resolve(
        new Response(
          JSON.stringify(path === "/api/sessions" ? [session] : session),
        ),
      );
    }),
  );
  const { result } = renderHook(() => useSessionHistory());
  act(() => {
    result.current.clear();
  });
  await act(async () => {
    finish(new Response(JSON.stringify(history)));
    await Promise.resolve();
  });
  expect(result.current.restored).toBeNull();
  expect(result.current.loading).toBe(false);
});
