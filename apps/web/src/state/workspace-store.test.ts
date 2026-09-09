import { describe, expect, it, vi } from "vitest";

import { WorkspaceStore } from "./workspace-store";

describe("workspace store", () => {
  it("coalesces token notifications to one animation frame", () => {
    const frame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation(() => 1);
    const store = new WorkspaceStore();
    const listener = vi.fn();
    store.subscribe(listener);

    store.queueNotification();
    store.queueNotification();

    expect(listener).not.toHaveBeenCalled();
    store.flushNotification();
    expect(listener).toHaveBeenCalledTimes(1);
    frame.mockRestore();
  });
});
