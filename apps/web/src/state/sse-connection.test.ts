import { describe, expect, it } from "vitest";

import { TaskEventConnection, type TaskStreamEvent } from "./sse-connection";

class FakeEventSource {
  readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  addEventListener(
    type: string,
    listener: (event: MessageEvent) => void,
  ): void {
    const values = this.listeners.get(type) ?? new Set();
    values.add(listener);
    this.listeners.set(type, values);
  }

  emit(
    type: string,
    event: TaskStreamEvent,
    lastEventId = "1700000000000-0",
  ): void {
    const message = new MessageEvent(type, {
      data: JSON.stringify(event),
      lastEventId,
    });
    this.listeners.get(type)?.forEach((listener) => {
      listener(message);
    });
  }

  close(): void {
    this.closed = true;
  }
}

describe("TaskEventConnection", () => {
  it("deduplicates event IDs and reports degraded reconnect state", () => {
    const source = new FakeEventSource();
    const events: TaskStreamEvent[] = [];
    const states: string[] = [];
    const connection = new TaskEventConnection(
      "task_12345678",
      {
        onEvent: (event) => events.push(event),
        onState: (state) => states.push(state),
      },
      () => source as unknown as EventSource,
    );

    connection.open();
    source.onopen?.();
    source.emit("analysis_progress", {
      eventId: "evt_progress_12345678",
      sequence: 1,
      sessionId: "ses_12345678",
      taskId: "task_12345678",
      occurredAt: "2026-07-27T00:00:00Z",
      type: "analysis_progress",
      payload: {
        milestone: "Inspecting workbook",
        detail: null,
        state: "running",
      },
      provenanceRefs: [],
    });
    source.emit("analysis_progress", {
      eventId: "evt_progress_12345678",
      sequence: 1,
      sessionId: "ses_12345678",
      taskId: "task_12345678",
      occurredAt: "2026-07-27T00:00:00Z",
      type: "analysis_progress",
      payload: { milestone: "Duplicate", detail: null, state: "running" },
      provenanceRefs: [],
    });
    source.onerror?.();
    source.onerror?.();
    connection.close();

    expect(events).toHaveLength(1);
    expect(events[0]?.payload).toEqual({
      milestone: "Inspecting workbook",
      detail: null,
      state: "running",
    });
    expect(states).toEqual([
      "connecting",
      "connected",
      "connecting",
      "degraded",
    ]);
    expect(source.closed).toBe(true);
  });
});

describe("TaskEventConnection terminal handling", () => {
  it.each(["1700000000000-0", ""])(
    "stops listening on a terminal event with SSE cursor %j",
    (lastEventId) => {
      const source = new FakeEventSource();
      const states: string[] = [];
      const connection = new TaskEventConnection(
        "task_12345678",
        {
          onEvent: () => undefined,
          onState: (state) => states.push(state),
        },
        () => source as unknown as EventSource,
      );

      connection.open();
      source.onopen?.();
      source.emit(
        "run.completed",
        {
          eventId: "evt_completed_12345678",
          sequence: 2,
          sessionId: "ses_12345678",
          taskId: "task_12345678",
          occurredAt: "2026-07-27T00:00:00Z",
          type: "run.completed",
          payload: { status: "completed", finalMessageId: "msg_12345678" },
          provenanceRefs: [],
        },
        lastEventId,
      );

      expect(source.closed).toBe(true);
      expect(states.at(-1)).toBe("closed");
    },
  );
});
