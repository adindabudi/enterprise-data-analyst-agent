import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAnalysisSession,
  readUploadStatus,
  uploadAnalysisInput,
  type AnalysisUpload,
} from "../api/analysis";
import { useAnalysisUpload } from "./useAnalysisUpload";

vi.mock("../api/analysis", () => ({
  createAnalysisSession: vi.fn(),
  uploadAnalysisInput: vi.fn(),
  readUploadStatus: vi.fn(),
}));

const sessionId = "ses_upload_12345678";
const selectedUpload: AnalysisUpload = {
  uploadId: "upl_upload_12345678",
  displayName: "revenue.csv",
  state: "scanning",
};
const file = new File(["revenue\n42"], "revenue.csv", { type: "text/csv" });

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Value>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  vi.mocked(createAnalysisSession).mockResolvedValue(sessionId);
  vi.mocked(uploadAnalysisInput).mockResolvedValue(selectedUpload);
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("analysis upload lifecycle", () => {
  it("does not upload or restore a session after removal during session creation", async () => {
    const session = deferred<string>();
    vi.mocked(createAnalysisSession).mockReturnValue(session.promise);
    const onSession = vi.fn();
    const { result } = renderHook(() =>
      useAnalysisUpload(null, "Analysis", onSession),
    );
    let operation!: Promise<void>;
    act(() => {
      operation = result.current.attach(file);
    });
    expect(result.current.upload?.state).toBe("pending");
    act(() => {
      result.current.clear();
    });
    await act(async () => {
      session.resolve(sessionId);
      await operation;
    });
    expect(onSession).not.toHaveBeenCalled();
    expect(uploadAnalysisInput).not.toHaveBeenCalled();
    expect(result.current.upload).toBeUndefined();
  });

  it("does not restore an upload after removal while the POST is in flight", async () => {
    const upload = deferred<AnalysisUpload>();
    vi.mocked(uploadAnalysisInput).mockReturnValue(upload.promise);
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    let operation!: Promise<void>;
    act(() => {
      operation = result.current.attach(file);
    });
    expect(result.current.upload?.state).toBe("uploading");
    act(() => {
      result.current.clear();
    });
    await act(async () => {
      upload.resolve({ ...selectedUpload, state: "clean" });
      await operation;
    });
    expect(result.current.upload).toBeUndefined();
    expect(result.current.inputUploadIds).toEqual([]);
  });

  it("never retries an ambiguous upload POST or treats it as clean", async () => {
    vi.mocked(uploadAnalysisInput).mockRejectedValue(
      new TypeError("Network error"),
    );
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    expect(result.current.upload?.state).toBe("error");
    expect(result.current.inputUploadIds).toEqual([]);
    await act(async () => {
      await result.current.refresh();
    });
    expect(uploadAnalysisInput).toHaveBeenCalledTimes(1);
    expect(readUploadStatus).not.toHaveBeenCalled();
    expect(createAnalysisSession).not.toHaveBeenCalled();
  });

  it.each(["scanning", "rejected", "scan_failed"] as const)(
    "does not submit a %s upload",
    async (state) => {
      vi.mocked(uploadAnalysisInput).mockResolvedValue({
        ...selectedUpload,
        state,
      });
      const { result } = renderHook(() =>
        useAnalysisUpload(sessionId, "Analysis", vi.fn()),
      );
      await act(async () => {
        await result.current.attach(file);
      });
      expect(result.current.upload?.state).toBe(state);
      expect(result.current.inputUploadIds).toEqual([]);
      expect(readUploadStatus).not.toHaveBeenCalled();
    },
  );

  it("automatically polls a scanning upload until it is clean", async () => {
    vi.mocked(readUploadStatus).mockResolvedValue({
      ...selectedUpload,
      state: "clean",
    });
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });

    expect(result.current.inputUploadIds).toEqual([]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(readUploadStatus).toHaveBeenCalledWith(
      sessionId,
      selectedUpload.uploadId,
    );
    expect(result.current.upload?.state).toBe("clean");
    expect(result.current.inputUploadIds).toEqual([selectedUpload.uploadId]);
  });

  it("stops polling when a scanning upload is cleared", async () => {
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    act(() => {
      result.current.clear();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(readUploadStatus).not.toHaveBeenCalled();
    expect(result.current.upload).toBeUndefined();
  });

  it("keeps a rejected scan removable and unavailable for submission", async () => {
    vi.mocked(readUploadStatus).mockResolvedValue({
      ...selectedUpload,
      state: "rejected",
    });
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(result.current.upload).toMatchObject({
      state: "rejected",
    });
    expect(result.current.inputUploadIds).toEqual([]);
    act(() => {
      result.current.clear();
    });
    expect(result.current.upload).toBeUndefined();
  });

  it("bounds refresh to one GET and preserves the identity for an explicit retry", async () => {
    const scan = deferred<AnalysisUpload>();
    vi.mocked(readUploadStatus)
      .mockReturnValueOnce(scan.promise)
      .mockResolvedValueOnce({ ...selectedUpload, state: "clean" });
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    let operation!: Promise<void>;
    act(() => {
      operation = result.current.refresh();
      void result.current.refresh();
    });
    expect(readUploadStatus).toHaveBeenCalledTimes(1);
    expect(result.current.refreshing).toBe(true);
    await act(async () => {
      scan.reject(new Error("Unavailable"));
      await operation;
    });
    expect(result.current.upload).toMatchObject({
      uploadId: selectedUpload.uploadId,
      state: "error",
    });
    expect(result.current.inputUploadIds).toEqual([]);
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.inputUploadIds).toEqual([selectedUpload.uploadId]);
    expect(readUploadStatus).toHaveBeenCalledTimes(2);
    expect(uploadAnalysisInput).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale status response after another file is selected", async () => {
    const scan = deferred<AnalysisUpload>();
    vi.mocked(readUploadStatus).mockReturnValue(scan.promise);
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    let operation!: Promise<void>;
    act(() => {
      operation = result.current.refresh();
    });
    const replacement = {
      ...selectedUpload,
      uploadId: "upl_replacement_12345678",
      displayName: "replacement.csv",
    };
    vi.mocked(uploadAnalysisInput).mockResolvedValue(replacement);
    await act(async () => {
      await result.current.attach(new File(["data"], replacement.displayName));
    });
    await act(async () => {
      scan.resolve({ ...selectedUpload, state: "clean" });
      await operation;
    });
    expect(result.current.upload).toMatchObject(replacement);
    expect(result.current.inputUploadIds).toEqual([]);
  });

  it("rejects a clean status for a different upload", async () => {
    vi.mocked(readUploadStatus).mockResolvedValue({
      ...selectedUpload,
      uploadId: "upl_other_12345678",
      state: "clean",
    });
    const { result } = renderHook(() =>
      useAnalysisUpload(sessionId, "Analysis", vi.fn()),
    );
    await act(async () => {
      await result.current.attach(file);
    });
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.upload?.state).toBe("error");
    expect(result.current.inputUploadIds).toEqual([]);
  });
});
