import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Textarea,
  Tooltip,
} from "@fluentui/react-components";
import {
  AddRegular,
  AttachRegular,
  SendRegular,
  StopRegular,
} from "@fluentui/react-icons";
import { useRef, useState } from "react";

import { InputUploadStatus } from "./InputUploadStatus";
import type { InputUpload } from "./useAnalysisUpload";

export type ComposerRequest = Record<string, never>;

const SCAN_IN_PROGRESS = new Set<InputUpload["state"]>([
  "pending",
  "uploading",
  "scanning",
]);

/** Why a message cannot be sent with this attachment, in words that say what to do next. */
export function blockedUploadMessage(
  attachment: InputUpload | undefined,
): string {
  return attachment !== undefined && SCAN_IN_PROGRESS.has(attachment.state)
    ? "Wait for the file scan to finish"
    : "Remove the file to send without it";
}

type ComposerProps = {
  disabled: boolean;
  running?: boolean;
  autoFocus?: boolean;
  onSend?: (message: string, request: ComposerRequest) => void;
  onStop?: () => void;
  onAttach?: (file: File) => void;
  attachment?: InputUpload | undefined;
  refreshingAttachment?: boolean;
  onRefreshAttachment?: () => void;
  onClearAttachment?: () => void;
  onBlockedUploadSend?: () => void;
};

export function Composer({
  disabled,
  running = false,
  autoFocus = false,
  onSend,
  onStop,
  onAttach,
  attachment,
  refreshingAttachment,
  onRefreshAttachment,
  onClearAttachment,
  onBlockedUploadSend,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadBlocked =
    attachment !== undefined && attachment.state !== "clean";
  const sendLabel = uploadBlocked
    ? blockedUploadMessage(attachment)
    : running
      ? "Send to the running analysis"
      : "Send message";
  const clearContext = (): void => {
    onClearAttachment?.();
    if (fileInput.current) fileInput.current.value = "";
  };
  const send = (): void => {
    const message = value.trim();
    if (!message || disabled) return;
    if (uploadBlocked) {
      onBlockedUploadSend?.();
      return;
    }
    onSend?.(message, {});
    setValue("");
    clearContext();
  };

  return (
    <section aria-label="Analysis composer" className="composer">
      <div className="composer__input">
        <Textarea
          aria-label="Analysis request"
          autoFocus={autoFocus}
          value={value}
          onChange={(_, data) => {
            setValue(data.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          placeholder="Ask about your analysis"
          disabled={disabled && !running}
        />
        {attachment && (
          <InputUploadStatus
            upload={attachment}
            refreshing={refreshingAttachment}
            onRefresh={onRefreshAttachment}
            onRemove={clearContext}
          />
        )}
      </div>
      <div
        className="composer__commands"
        role="toolbar"
        aria-label="Analysis commands"
      >
        <input
          ref={fileInput}
          className="visually-hidden"
          type="file"
          aria-label="Choose analysis input"
          disabled={disabled || running || !onAttach}
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            if (!file || !onAttach) return;
            onAttach(file);
          }}
        />
        <Menu>
          <MenuTrigger disableButtonEnhancement>
            <Button
              appearance="subtle"
              className="composer__command"
              icon={<AddRegular />}
              aria-label="Add context"
              disabled={disabled || running}
            />
          </MenuTrigger>
          <MenuPopover>
            <MenuList>
              <MenuItem
                icon={<AttachRegular />}
                disabled={!onAttach}
                onClick={() => {
                  fileInput.current?.click();
                }}
              >
                Attach file
              </MenuItem>
            </MenuList>
          </MenuPopover>
        </Menu>
        <Tooltip content={sendLabel} relationship="label">
          <Button
            appearance="primary"
            className="composer__command composer__send"
            icon={<SendRegular />}
            aria-label={sendLabel}
            disabled={disabled || uploadBlocked || !value.trim()}
            onClick={send}
          />
        </Tooltip>
        <Tooltip content="Stop current task" relationship="label">
          <Button
            appearance="subtle"
            className="composer__command composer__stop"
            icon={<StopRegular />}
            aria-label="Stop current task"
            disabled={!running || !onStop}
            onClick={onStop}
          />
        </Tooltip>
      </div>
    </section>
  );
}
