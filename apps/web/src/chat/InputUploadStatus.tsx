import { Button, Text, Tooltip } from "@fluentui/react-components";
import { ArrowClockwiseRegular, DismissRegular } from "@fluentui/react-icons";

import type { InputUpload } from "./useAnalysisUpload";

const labels: Record<InputUpload["state"], string> = {
  pending: "Pending upload",
  uploading: "Uploading",
  scanning: "Checking the file for malware…",
  clean: "Clean",
  rejected: "Rejected",
  scan_failed: "Scan failed",
  error: "Upload error",
};

export function InputUploadStatus({
  upload,
  refreshing = false,
  onRefresh,
  onRemove,
}: {
  upload: InputUpload;
  refreshing?: boolean | undefined;
  onRefresh?: (() => void) | undefined;
  onRemove?: () => void;
}) {
  const canRefresh =
    upload.uploadId &&
    ["scanning", "scan_failed", "error"].includes(upload.state);
  return (
    <div className="input-upload">
      <div
        className="input-upload__status"
        role="status"
        aria-label="Input upload"
      >
        <Text weight="semibold">{upload.displayName}</Text>
        <Text size={200}>{labels[upload.state]}</Text>
        {refreshing && <Text size={200}>Refreshing upload status</Text>}
        {upload.error && <Text size={200}>{upload.error}</Text>}
      </div>
      {canRefresh && onRefresh && (
        <Tooltip content="Refresh upload status" relationship="label">
          <Button
            appearance="subtle"
            icon={<ArrowClockwiseRegular />}
            aria-label="Refresh upload status"
            disabled={refreshing}
            onClick={onRefresh}
          />
        </Tooltip>
      )}
      {onRemove && (
        <Tooltip content="Remove attachment" relationship="label">
          <Button
            appearance="subtle"
            icon={<DismissRegular />}
            aria-label="Remove attachment"
            onClick={onRemove}
          />
        </Tooltip>
      )}
    </div>
  );
}
