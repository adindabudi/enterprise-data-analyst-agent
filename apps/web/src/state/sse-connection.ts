import { parseActivityEvent, type ActivityEvent } from "@eda/contracts";

export type TaskStreamEvent = ActivityEvent;

export type TaskConnectionState =
  "connecting" | "connected" | "degraded" | "closed";

type ConnectionHandlers = {
  onEvent: (event: TaskStreamEvent) => void;
  onState: (state: TaskConnectionState) => void;
};

type EventSourceFactory = (url: string) => EventSource;

const EVENT_TYPES: TaskStreamEvent["type"][] = [
  "analysis_progress",
  "message.delta",
  "attempt.superseded",
  "auth.required",
  "task.checkpointed",
  "todo.updated",
  "artifact.ready",
  "run.completed",
  "run.failed",
  "run.cancelled",
];

// The server ends the stream after a terminal event, which EventSource would
// otherwise treat as a drop and retry forever.
const TERMINAL_EVENT_TYPES = new Set<TaskStreamEvent["type"]>([
  "run.completed",
  "run.failed",
  "run.cancelled",
]);

export class TaskEventConnection {
  private source: EventSource | undefined;
  private readonly seenEventIds = new Set<string>();
  private errors = 0;

  constructor(
    private readonly taskId: string,
    private readonly handlers: ConnectionHandlers,
    private readonly createEventSource: EventSourceFactory = (url) =>
      new EventSource(url),
  ) {}

  open(): void {
    this.closeSource();
    this.handlers.onState("connecting");
    const source = this.createEventSource(
      `/api/tasks/${encodeURIComponent(this.taskId)}/events`,
    );
    this.source = source;
    source.onopen = () => {
      this.errors = 0;
      this.handlers.onState("connected");
    };
    source.onerror = () => {
      this.errors += 1;
      this.handlers.onState(this.errors > 1 ? "degraded" : "connecting");
    };
    EVENT_TYPES.forEach((type) => {
      source.addEventListener(type, (rawEvent) => {
        this.handleEvent(type, rawEvent as MessageEvent<string>);
      });
    });
  }

  close(): void {
    this.closeSource();
  }

  private closeSource(): void {
    this.source?.close();
    this.source = undefined;
  }

  private handleEvent(
    type: TaskStreamEvent["type"],
    message: MessageEvent<string>,
  ): void {
    let value: unknown;
    try {
      value = JSON.parse(message.data);
    } catch {
      return;
    }
    let event: ActivityEvent;
    try {
      event = parseActivityEvent(value);
    } catch {
      return;
    }
    if (event.type !== type || this.seenEventIds.has(event.eventId)) return;
    this.seenEventIds.add(event.eventId);
    this.handlers.onEvent(event);
    if (TERMINAL_EVENT_TYPES.has(event.type)) {
      this.closeSource();
      this.handlers.onState("closed");
    }
  }
}
