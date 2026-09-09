import { describe, expect, it } from "vitest";

import { parseActivityEvent } from "../src/validate";

describe("parseActivityEvent", () => {
  it("accepts a typed message delta", () => {
    const event = parseActivityEvent({
      eventId: "evt_01HZZZZZZZZZZZZZZZZZZZZZZZ",
      sequence: 1,
      responseAttemptId: "attempt-1",
      attemptSequence: 0,
      sessionId: "ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
      taskId: "task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
      occurredAt: "2026-07-23T10:15:30Z",
      type: "message.delta",
      payload: { delta: "hello" },
      provenanceRefs: [],
    });
    expect(event.type).toBe("message.delta");
  });

  it("rejects unknown credential-shaped payload fields", () => {
    expect(() =>
      parseActivityEvent({
        eventId: "evt_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        sequence: 1,
        responseAttemptId: "attempt-1",
        attemptSequence: 0,
        sessionId: "ses_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        taskId: "task_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        occurredAt: "2026-07-23T10:15:30Z",
        type: "message.delta",
        payload: { delta: "hello", accessToken: "secret" },
        provenanceRefs: [],
      }),
    ).toThrow("Invalid activity event");
  });
});
