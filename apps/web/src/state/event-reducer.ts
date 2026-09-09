export type TaskEvent =
  | {
      eventId: string;
      type: "message.delta";
      responseAttemptId: string;
      attemptSequence: number;
      payload: { delta: string };
    }
  | {
      eventId: string;
      type: "attempt.superseded";
      payload: { supersededAttemptId: string; replacementAttemptId: string };
    }
  | {
      eventId: string;
      type: "run.completed";
      payload: { finalMessageId: string };
    }
  | {
      eventId: string;
      type: "auth.required";
      payload: { actionPath?: string };
    }
  | {
      eventId: string;
      type: "task.checkpointed";
      payload: { status: string; checkpointSequence: number };
    };

export type FabricAuthViewAction = {
  eventId: string;
  actionPath: "/api/fabric/auth/start";
};

export type TaskViewState = {
  seenEventIds: ReadonlySet<string>;
  attemptBuffers: Readonly<Record<string, { sequence: number; text: string }>>;
  hiddenAttempts: ReadonlySet<string>;
  provisionalText: string;
  finalMessageId?: string;
  fabricAuthAction: FabricAuthViewAction | undefined;
};

export function initialTaskView(): TaskViewState {
  return {
    seenEventIds: new Set(),
    attemptBuffers: {},
    hiddenAttempts: new Set(),
    provisionalText: "",
    fabricAuthAction: undefined,
  };
}

export function reduceEvent(
  state: TaskViewState,
  event: TaskEvent,
): TaskViewState {
  if (state.seenEventIds.has(event.eventId)) return state;
  const seenEventIds = new Set(state.seenEventIds);
  seenEventIds.add(event.eventId);
  if (event.type === "attempt.superseded") {
    const attemptBuffers = Object.fromEntries(
      Object.entries(state.attemptBuffers).filter(
        ([attemptId]) => attemptId !== event.payload.supersededAttemptId,
      ),
    );
    const hiddenAttempts = new Set(state.hiddenAttempts);
    hiddenAttempts.add(event.payload.supersededAttemptId);
    return {
      ...state,
      seenEventIds,
      attemptBuffers,
      hiddenAttempts,
      provisionalText: renderBuffers(attemptBuffers),
    };
  }
  if (event.type === "auth.required") {
    const payloadKeys = Object.keys(event.payload);
    let fabricAuthAction: FabricAuthViewAction | undefined =
      state.fabricAuthAction;
    if (
      payloadKeys.length === 1 &&
      event.payload.actionPath === "/api/fabric/auth/start"
    ) {
      fabricAuthAction = {
        eventId: event.eventId,
        actionPath: "/api/fabric/auth/start",
      };
    }
    return { ...state, seenEventIds, fabricAuthAction };
  }
  if (event.type === "task.checkpointed") {
    return {
      ...state,
      seenEventIds,
      fabricAuthAction:
        event.payload.status === "blocked_auth"
          ? state.fabricAuthAction
          : undefined,
    };
  }
  if (event.type === "run.completed") {
    return {
      ...state,
      seenEventIds,
      attemptBuffers: {},
      provisionalText: "",
      finalMessageId: event.payload.finalMessageId,
      fabricAuthAction: undefined,
    };
  }
  if (state.hiddenAttempts.has(event.responseAttemptId))
    return { ...state, seenEventIds };
  const prior = state.attemptBuffers[event.responseAttemptId];
  if (prior && event.attemptSequence <= prior.sequence)
    return { ...state, seenEventIds };
  const attemptBuffers = {
    ...state.attemptBuffers,
    [event.responseAttemptId]: {
      sequence: event.attemptSequence,
      text: `${prior?.text ?? ""}${event.payload.delta}`,
    },
  };
  return {
    ...state,
    seenEventIds,
    attemptBuffers,
    provisionalText: renderBuffers(attemptBuffers),
  };
}

function renderBuffers(
  buffers: Readonly<Record<string, { sequence: number; text: string }>>,
): string {
  return Object.values(buffers)
    .sort((left, right) => left.sequence - right.sequence)
    .map((buffer) => buffer.text)
    .join("");
}
