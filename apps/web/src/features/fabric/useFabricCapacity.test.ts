import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getFabricCapacityState,
  type FabricCapacityState,
} from "../../api/fabric";
import { PAUSED_RECHECK_MS, useFabricCapacity } from "./useFabricCapacity";

vi.mock("../../api/fabric", () => ({
  getFabricCapacityState: vi.fn(),
}));

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("Fabric capacity", () => {
  it("reads nothing while the source cannot answer queries", () => {
    const { result } = renderHook(() => useFabricCapacity(false));

    expect(result.current.capacity).toBeUndefined();
    expect(getFabricCapacityState).not.toHaveBeenCalled();
  });

  it("says it is checking until the first answer arrives", async () => {
    const answer = deferred<FabricCapacityState>();
    vi.mocked(getFabricCapacityState).mockReturnValue(answer.promise);
    const { result } = renderHook(() => useFabricCapacity(true));

    expect(result.current.capacity).toBe("checking");
    answer.resolve("paused");
    await settle();

    expect(result.current.capacity).toBe("paused");
  });

  it("falls back to unknown only when the first look fails", async () => {
    vi.mocked(getFabricCapacityState)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce("paused")
      .mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useFabricCapacity(true));
    await settle();
    expect(result.current.capacity).toBe("unknown");

    act(() => {
      result.current.refresh();
    });
    await settle();
    expect(result.current.capacity).toBe("paused");

    act(() => {
      result.current.refresh();
    });
    await settle();
    // A failed look proves nothing, so the pause it last saw still stands.
    expect(result.current.capacity).toBe("paused");
  });

  it("looks again after a run even while an earlier look is on its way", async () => {
    const first = deferred<FabricCapacityState>();
    vi.mocked(getFabricCapacityState)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce("paused");
    const { result } = renderHook(() => useFabricCapacity(true));

    act(() => {
      result.current.refresh();
    });
    first.resolve("active");
    await settle();
    await settle();

    expect(getFabricCapacityState).toHaveBeenCalledTimes(2);
    expect(result.current.capacity).toBe("paused");
  });

  it("keeps looking while paused, so a resumed capacity shows up", async () => {
    vi.mocked(getFabricCapacityState)
      .mockResolvedValueOnce("paused")
      .mockResolvedValueOnce("active");
    const { result } = renderHook(() => useFabricCapacity(true));
    await settle();
    expect(result.current.capacity).toBe("paused");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAUSED_RECHECK_MS);
    });
    await settle();
    expect(result.current.capacity).toBe("active");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PAUSED_RECHECK_MS * 3);
    });
    // A running capacity is not polled.
    expect(getFabricCapacityState).toHaveBeenCalledTimes(2);
  });

  it("looks again when the user comes back to the page", async () => {
    vi.mocked(getFabricCapacityState)
      .mockResolvedValueOnce("active")
      .mockResolvedValueOnce("paused");
    const { result } = renderHook(() => useFabricCapacity(true));
    await settle();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await settle();

    expect(result.current.capacity).toBe("paused");
  });

  it("forgets the capacity once the source stops answering queries", async () => {
    vi.mocked(getFabricCapacityState).mockResolvedValue("paused");
    const { result, rerender } = renderHook(
      ({ enabled }) => useFabricCapacity(enabled),
      { initialProps: { enabled: true } },
    );
    await settle();
    expect(result.current.capacity).toBe("paused");

    rerender({ enabled: false });

    expect(result.current.capacity).toBeUndefined();
  });
});
