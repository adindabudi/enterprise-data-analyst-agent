import {
  Button,
  Dialog,
  DialogSurface,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import { ArrowClockwiseRegular, DismissRegular } from "@fluentui/react-icons";
import { useEffect, useReducer, useState } from "react";

import { artifactDownloadPath, type PublishedArtifact } from "../api/analysis";
import { ArtifactPreview } from "./ArtifactPreview";

type PreviewResult =
  | { attempt: number; state: "ready"; content: string }
  | { attempt: number; state: "error" };

export function ArtifactPreviewDialog({
  taskId,
  artifact,
  onClose,
}: {
  taskId: string;
  artifact: PublishedArtifact;
  onClose: () => void;
}) {
  const [attempt, retry] = useReducer((current: number) => current + 1, 0);
  const [result, setResult] = useState<PreviewResult>();

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void fetch(artifactDownloadPath(taskId, artifact), {
      credentials: "same-origin",
      headers: { Accept: "text/html" },
      redirect: "error",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok || response.redirected)
          throw new Error("Preview request failed");
        const content = await response.text();
        if (!content.trim()) throw new Error("Preview content is empty");
        if (active) setResult({ attempt, state: "ready", content });
      })
      .catch(() => {
        if (active) setResult({ attempt, state: "error" });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [taskId, artifact, attempt]);

  const current = result?.attempt === attempt ? result : undefined;
  return (
    <Dialog
      open
      onOpenChange={(_, data) => {
        if (!data.open) onClose();
      }}
    >
      <DialogSurface
        aria-label={`Preview ${artifact.displayName}`}
        className="preview-dialog"
      >
        <header className="preview-dialog__header">
          <Text as="h2" size={400} weight="semibold">
            {artifact.displayName}
          </Text>
          <Tooltip content="Close preview" relationship="label">
            <Button
              appearance="subtle"
              icon={<DismissRegular />}
              aria-label="Close preview"
              onClick={onClose}
            />
          </Tooltip>
        </header>
        <div className="preview-dialog__content">
          {!current ? (
            <div className="preview-dialog__state">
              <Text role="status">Loading preview</Text>
            </div>
          ) : current.state === "error" ? (
            <div className="preview-dialog__state">
              <Text role="alert">Preview could not be loaded.</Text>
              <Button
                appearance="subtle"
                icon={<ArrowClockwiseRegular />}
                onClick={retry}
              >
                Retry preview
              </Button>
            </div>
          ) : (
            <ArtifactPreview
              artifact={{
                id: artifact.artifactId,
                kind: "html",
                status: "ready",
                content: current.content,
              }}
            />
          )}
        </div>
      </DialogSurface>
    </Dialog>
  );
}
