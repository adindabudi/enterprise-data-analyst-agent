import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { TaskSummary } from "@eda/contracts";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskStreamEvent } from "../state/sse-connection";
import { DesktopWorkspace } from "./DesktopWorkspace";
import { MobileWorkspace } from "./MobileWorkspace";

let historyPending = false;
let restoredStatus: TaskSummary["status"] = "analyzing";

class SnapshotEventSource {
  static current: SnapshotEventSource | undefined;
  readonly listeners = new Map<string, EventListener>();
  readonly close = vi.fn();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    SnapshotEventSource.current = this;
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, listener);
  }

  emit(event: TaskStreamEvent): void {
    if (
      event.type === "task.checkpointed" ||
      event.type === "run.completed" ||
      event.type === "run.failed" ||
      event.type === "run.cancelled"
    )
      restoredStatus = event.payload.status;
    this.listeners.get(event.type)?.(
      new MessageEvent(event.type, {
        data: JSON.stringify(event),
        lastEventId: "",
      }),
    );
  }
}

const envelope = {
  eventId: "evt_snapshot_12345678",
  sequence: 1,
  sessionId: "ses_resume_12345678",
  taskId: "task_resume_12345678",
  occurredAt: "2026-09-07T00:00:00Z",
  provenanceRefs: [],
};

beforeEach(() => {
  historyPending = false;
  restoredStatus = "analyzing";
  window.history.replaceState(null, "", "/?task=task_resume_12345678");
  vi.stubGlobal("EventSource", SnapshotEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = input instanceof Request ? input.url : String(input);
      if (path.endsWith("/history") && historyPending)
        return new Promise<Response>(() => undefined);
      const task = {
        taskId: envelope.taskId,
        sessionId: envelope.sessionId,
        status: restoredStatus,
        finalMessageId: null,
        sourceMessageId: null,
        createdAt: envelope.occurredAt,
        updatedAt: envelope.occurredAt,
      };
      const body =
        path === "/api/sessions"
          ? []
          : path.endsWith("/history")
            ? { messages: [], tasks: [task] }
            : path === `/api/sessions/${envelope.sessionId}`
              ? {
                  sessionId: envelope.sessionId,
                  title: "Saved analysis",
                  lastActivityAt: envelope.occurredAt,
                }
              : path === `/api/tasks/${envelope.taskId}`
                ? task
                : path.endsWith("/todos")
                  ? { items: [] }
                  : path.endsWith("/provenance")
                    ? { sourceQueries: [], artifacts: [] }
                    : path.includes("/messages/")
                      ? {
                          messageId: "msg_resume_12345678",
                          role: "assistant",
                          text: "The workbook is ready.",
                        }
                      : { artifacts: [] };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  SnapshotEventSource.current = undefined;
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe.each([
  ["desktop", DesktopWorkspace],
  ["mobile", MobileWorkspace],
] as const)("%s task resume", (_layout, Workspace) => {
  it("blocks sending while restoring the canonical task state", async () => {
    const user = userEvent.setup();
    historyPending = true;
    render(<Workspace />);

    expect(
      screen.getByRole("button", { name: "Restoring analysis" }),
    ).toBeVisible();
    const composer = screen.getByRole("textbox", { name: "Analysis request" });
    await user.type(composer, "How many rooms?{Enter}");

    expect(composer).toHaveValue("How many rooms?");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([, options]) => options?.method === "POST"),
    ).toBe(false);
  });

  it("shows the restored phase while waiting for live progress", async () => {
    render(<Workspace />);
    await waitFor(() => {
      expect(SnapshotEventSource.current).toBeDefined();
    });

    act(() => {
      SnapshotEventSource.current?.onopen?.();
      SnapshotEventSource.current?.emit({
        ...envelope,
        type: "task.checkpointed",
        payload: { status: "analyzing", checkpointSequence: 3 },
      });
    });

    expect(
      screen.getByRole("button", { name: "Analyzing data" }),
    ).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "Query in progress" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeEnabled();
    expect(
      screen.queryByText("Ready to begin an analysis"),
    ).not.toBeInTheDocument();
  });

  it("keeps loading after a tool completes until the task is terminal", async () => {
    const user = userEvent.setup();
    render(<Workspace />);
    await waitFor(() => {
      expect(SnapshotEventSource.current).toBeDefined();
    });

    act(() => {
      SnapshotEventSource.current?.onopen?.();
      SnapshotEventSource.current?.emit({
        ...envelope,
        type: "analysis_progress",
        payload: {
          milestone: "Publishing output",
          state: "completed",
          detail: "Finished publish_artifact.",
        },
      });
    });

    expect(
      screen.getByRole("progressbar", { name: "Query in progress" }),
    ).toBeVisible();
    expect(
      screen.queryByText("completed", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Publishing output" }));
    expect(screen.getByText("Finished publish_artifact.")).toBeVisible();

    act(() => {
      SnapshotEventSource.current?.emit({
        ...envelope,
        eventId: "evt_terminal_12345678",
        sequence: 2,
        type: "run.completed",
        payload: { status: "completed", finalMessageId: null },
      });
    });

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("completed", { exact: true })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeDisabled();
  });

  it.each(["completed", "failed", "cancelled"] as const)(
    "settles a %s snapshot without a Redis cursor",
    async (status) => {
      render(<Workspace />);
      await waitFor(() => {
        expect(SnapshotEventSource.current).toBeDefined();
      });

      act(() => {
        SnapshotEventSource.current?.onopen?.();
        SnapshotEventSource.current?.emit({
          ...envelope,
          type: "task.checkpointed",
          payload: { status, checkpointSequence: 3 },
        });
        SnapshotEventSource.current?.emit({
          ...envelope,
          eventId: "evt_terminal_12345678",
          sequence: 2,
          type: `run.${status}`,
          payload: {
            status,
            finalMessageId:
              status === "completed" ? "msg_resume_12345678" : null,
          },
        });
      });

      expect(
        screen.getByRole("button", { name: "Stop current task" }),
      ).toBeDisabled();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(
        screen.queryByText("Live updates reconnecting"),
      ).not.toBeInTheDocument();
      expect(SnapshotEventSource.current?.close).toHaveBeenCalledOnce();
      if (status === "completed") {
        expect(await screen.findByText("The workbook is ready.")).toBeVisible();
      } else {
        expect(
          screen.getAllByText(`Analysis ${status}`).length,
        ).toBeGreaterThan(0);
      }
    },
  );
});
