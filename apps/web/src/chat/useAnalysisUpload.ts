import { useEffect, useRef, useState } from "react";

import {
  createAnalysisSession,
  readUploadStatus,
  uploadAnalysisInput,
  type AnalysisUpload,
} from "../api/analysis";

export type InputUpload = {
  displayName: string;
  state: AnalysisUpload["state"] | "pending" | "uploading" | "error";
  sessionId?: string;
  uploadId?: string;
  error?: string;
};

const POLL_BUDGET_MS = 120_000;
const FAST_POLL_MS = 2_000;
const SLOW_POLL_MS = 5_000;
const FAST_POLL_BUDGET_MS = 30_000;
const TERMINAL_STATES = new Set<InputUpload["state"]>([
  "clean",
  "rejected",
  "scan_failed",
  "error",
]);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function useAnalysisUpload(
  sessionId: string | null,
  title: string,
  onSession: (sessionId: string) => void,
) {
  const [upload, setUpload] = useState<InputUpload>();
  const [refreshing, setRefreshing] = useState(false);
  const generation = useRef(0);
  const refreshInFlight = useRef(false);
  const uploadRef = useRef<InputUpload | undefined>(undefined);

  const setCurrentUpload = (next: InputUpload | undefined): void => {
    uploadRef.current = next;
    setUpload(next);
  };

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const clear = (): void => {
    generation.current += 1;
    setRefreshing(false);
    setCurrentUpload(undefined);
  };

  const refreshUpload = async ({
    sessionId: ownerSession,
    uploadId,
    generation: expectedGeneration,
    surfaceError,
  }: {
    sessionId: string;
    uploadId: string;
    generation: number;
    surfaceError: boolean;
  }): Promise<AnalysisUpload | undefined> => {
    if (refreshInFlight.current) return undefined;
    refreshInFlight.current = true;
    setRefreshing(true);
    try {
      const result = await readUploadStatus(ownerSession, uploadId);
      if (result.uploadId !== uploadId)
        throw new Error("Upload identity changed");
      if (generation.current === expectedGeneration) {
        setCurrentUpload({ ...result, sessionId: ownerSession });
        return result;
      }
    } catch {
      if (generation.current === expectedGeneration && surfaceError) {
        setCurrentUpload({
          displayName: uploadRef.current?.displayName ?? "Selected file",
          sessionId: ownerSession,
          uploadId,
          state: "error",
          error:
            "Upload status could not be loaded. Refresh the status to try again.",
        });
      }
    } finally {
      refreshInFlight.current = false;
      if (generation.current === expectedGeneration) setRefreshing(false);
    }
    return undefined;
  };

  const pollUpload = async (
    ownerSession: string,
    uploadId: string,
    expectedGeneration: number,
  ): Promise<void> => {
    const startedAt = Date.now();
    while (generation.current === expectedGeneration) {
      const current = uploadRef.current;
      if (!current || current.uploadId !== uploadId) return;
      if (TERMINAL_STATES.has(current.state)) return;
      const elapsed = Date.now() - startedAt;
      if (elapsed >= POLL_BUDGET_MS) {
        setCurrentUpload({
          ...current,
          state: "error",
          error:
            "Upload status could not be loaded. Refresh the status to try again.",
        });
        return;
      }
      await sleep(elapsed < FAST_POLL_BUDGET_MS ? FAST_POLL_MS : SLOW_POLL_MS);
      if (generation.current !== expectedGeneration) return;
      const result = await refreshUpload({
        sessionId: ownerSession,
        uploadId,
        generation: expectedGeneration,
        surfaceError: false,
      });
      if (result && TERMINAL_STATES.has(result.state)) return;
    }
  };

  const attach = async (file: File): Promise<void> => {
    const currentGeneration = ++generation.current;
    const isCurrent = (): boolean => generation.current === currentGeneration;
    setRefreshing(false);
    setCurrentUpload({ displayName: file.name, state: "pending" });
    try {
      const ownerSession = sessionId ?? (await createAnalysisSession(title));
      if (!isCurrent()) return;
      onSession(ownerSession);
      setCurrentUpload({
        displayName: file.name,
        state: "uploading",
        sessionId: ownerSession,
      });
      const result = await uploadAnalysisInput(ownerSession, file);
      if (!isCurrent()) return;
      setCurrentUpload({ ...result, sessionId: ownerSession });
      if (result.state === "scanning") {
        void pollUpload(ownerSession, result.uploadId, currentGeneration);
      }
    } catch {
      if (!isCurrent()) return;
      setCurrentUpload({
        displayName: file.name,
        state: "error",
        error:
          "Upload could not be confirmed. Remove the attachment before choosing a file again.",
      });
    }
  };

  const refresh = async (): Promise<void> => {
    const current = uploadRef.current;
    if (!current?.sessionId || !current.uploadId || refreshInFlight.current)
      return;
    await refreshUpload({
      sessionId: current.sessionId,
      uploadId: current.uploadId,
      generation: generation.current,
      surfaceError: true,
    });
  };

  return {
    upload,
    refreshing,
    attach,
    clear,
    refresh,
    inputUploadIds:
      upload?.state === "clean" && upload.uploadId ? [upload.uploadId] : [],
  };
}
