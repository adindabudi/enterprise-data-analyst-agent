import { describe, expect, it } from "vitest";

import { initialTaskView, reduceEvent, type TaskEvent } from "./event-reducer";

const delta = (
  eventId: string,
  attempt: string,
  sequence: number,
  text: string,
): TaskEvent => ({
  eventId,
  type: "message.delta",
  responseAttemptId: attempt,
  attemptSequence: sequence,
  payload: { delta: text },
});

describe("task event reducer", () => {
  it("ignores an event ID already rendered", () => {
    const once = reduceEvent(
      initialTaskView(),
      delta("evt_1", "attempt-1", 1, "Hello"),
    );
    const twice = reduceEvent(once, delta("evt_1", "attempt-1", 1, "Hello"));

    expect(twice.provisionalText).toBe("Hello");
  });

  it("removes superseded provisional text rather than concatenating", () => {
    let state = reduceEvent(
      initialTaskView(),
      delta("evt_1", "attempt-1", 1, "Wrong"),
    );
    state = reduceEvent(state, {
      eventId: "evt_2",
      type: "attempt.superseded",
      payload: {
        supersededAttemptId: "attempt-1",
        replacementAttemptId: "attempt-2",
      },
    });
    state = reduceEvent(state, delta("evt_3", "attempt-2", 1, "Right"));

    expect(state.provisionalText).toBe("Right");
  });

  it("run.completed stores only final message ID", () => {
    const state = reduceEvent(initialTaskView(), {
      eventId: "evt_9",
      type: "run.completed",
      payload: { finalMessageId: "msg_final" },
    });

    expect(state.finalMessageId).toBe("msg_final");
    expect(JSON.stringify(state)).not.toContain("final response text");
  });

  it("retains identical text carried by distinct durable event IDs", () => {
    let state = reduceEvent(
      initialTaskView(),
      delta("evt_1", "attempt-1", 1, "the"),
    );
    state = reduceEvent(state, delta("evt_2", "attempt-1", 2, "the"));

    expect(state.provisionalText).toBe("thethe");
  });

  it("projects one exact relative Fabric auth action across replay", () => {
    const event: TaskEvent = {
      eventId: "evt_auth_1",
      type: "auth.required",
      payload: { actionPath: "/api/fabric/auth/start" },
    };

    const once = reduceEvent(initialTaskView(), event);
    const replayed = reduceEvent(once, event);

    expect(replayed.fabricAuthAction).toEqual({
      eventId: "evt_auth_1",
      actionPath: "/api/fabric/auth/start",
    });
  });

  it("rejects absolute action URLs and provider metadata", () => {
    const absolute = reduceEvent(initialTaskView(), {
      eventId: "evt_auth_2",
      type: "auth.required",
      payload: {
        actionPath:
          "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize",
      },
    });
    const provider = reduceEvent(initialTaskView(), {
      eventId: "evt_auth_3",
      type: "auth.required",
      payload: {
        actionPath: "/api/fabric/auth/start",
        provider: "semantic_model",
      },
    } as unknown as TaskEvent);

    expect(absolute.fabricAuthAction).toBeUndefined();
    expect(provider.fabricAuthAction).toBeUndefined();
  });

  it("clears the action after a resumed durable checkpoint", () => {
    const blocked = reduceEvent(initialTaskView(), {
      eventId: "evt_auth_4",
      type: "auth.required",
      payload: { actionPath: "/api/fabric/auth/start" },
    });
    const resumed = reduceEvent(blocked, {
      eventId: "evt_checkpoint_1",
      type: "task.checkpointed",
      payload: { status: "acquiring_data", checkpointSequence: 5 },
    });

    expect(resumed.fabricAuthAction).toBeUndefined();
  });
});
