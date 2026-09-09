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

export function useAnalysisUpload(
  sessionId: string | null,
  title: string,
  onSession: (sessionId: string) => void,
) {
  const [upload, setUpload] = useState<InputUpload>();
  const [refreshing, setRefreshing] = useState(false);
  const generation = useRef(0);
  const refreshInFlight = useRef(false);

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const clear = (): void => {
    generation.current += 1;
    refreshInFlight.current = false;
    setRefreshing(false);
    setUpload(undefined);
  };

  const attach = async (file: File): Promise<void> => {
    const currentGeneration = ++generation.current;
    const isCurrent = (): boolean => generation.current === currentGeneration;
    refreshInFlight.current = false;
    setRefreshing(false);
    setUpload({ displayName: file.name, state: "pending" });
    try {
      const ownerSession = sessionId ?? (await createAnalysisSession(title));
      if (!isCurrent()) return;
      onSession(ownerSession);
      setUpload({
        displayName: file.name,
        state: "uploading",
        sessionId: ownerSession,
      });
      const result = await uploadAnalysisInput(ownerSession, file);
      if (isCurrent()) setUpload({ ...result, sessionId: ownerSession });
    } catch {
      if (!isCurrent()) return;
      setUpload({
        displayName: file.name,
        state: "error",
        error:
          "Upload could not be confirmed. Remove the attachment before choosing a file again.",
      });
    }
  };

  const refresh = async (): Promise<void> => {
    if (!upload?.sessionId || !upload.uploadId || refreshInFlight.current)
      return;
    const currentGeneration = generation.current;
    refreshInFlight.current = true;
    setRefreshing(true);
    try {
      const result = await readUploadStatus(upload.sessionId, upload.uploadId);
      if (result.uploadId !== upload.uploadId)
        throw new Error("Upload identity changed");
      if (generation.current === currentGeneration)
        setUpload({ ...result, sessionId: upload.sessionId });
    } catch {
      if (generation.current === currentGeneration)
        setUpload({
          ...upload,
          state: "error",
          error:
            "Upload status could not be loaded. Refresh the status to try again.",
        });
    } finally {
      if (generation.current === currentGeneration) {
        refreshInFlight.current = false;
        setRefreshing(false);
      }
    }
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
