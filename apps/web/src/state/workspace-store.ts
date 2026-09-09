export type WorkspaceSnapshot = {
  connection: "idle" | "connecting" | "connected" | "degraded";
  selectedTab: "inputs" | "outputs" | "tasks" | "provenance";
};

const EMPTY_SNAPSHOT: WorkspaceSnapshot = {
  connection: "idle",
  selectedTab: "inputs",
};

export class WorkspaceStore {
  private snapshot: WorkspaceSnapshot = EMPTY_SNAPSHOT;
  private readonly listeners = new Set<() => void>();
  private notificationQueued = false;

  getSnapshot = (): WorkspaceSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  setConnection(connection: WorkspaceSnapshot["connection"]): void {
    this.snapshot = { ...this.snapshot, connection };
    this.queueNotification();
  }

  queueNotification(): void {
    if (this.notificationQueued) return;
    this.notificationQueued = true;
    window.requestAnimationFrame(() => {
      this.flushNotification();
    });
  }

  flushNotification(): void {
    if (!this.notificationQueued) return;
    this.notificationQueued = false;
    this.listeners.forEach((listener) => {
      listener();
    });
  }
}

export const workspaceStore = new WorkspaceStore();
