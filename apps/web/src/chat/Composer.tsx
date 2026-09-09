import {
  Button,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Text,
  Textarea,
  Tooltip,
} from "@fluentui/react-components";
import {
  AddRegular,
  AttachRegular,
  BrainCircuitRegular,
  DismissRegular,
  SendRegular,
  StopRegular,
} from "@fluentui/react-icons";
import { useRef, useState } from "react";

import { InputUploadStatus } from "./InputUploadStatus";
import type { InputUpload } from "./useAnalysisUpload";

export type ComposerRequest = {
  deepAnalysis: boolean;
};

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
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [deepAnalysis, setDeepAnalysis] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadBlocked =
    attachment !== undefined && attachment.state !== "clean";
  const clearContext = (): void => {
    onClearAttachment?.();
    setDeepAnalysis(false);
    if (fileInput.current) fileInput.current.value = "";
  };
  const send = (): void => {
    const message = value.trim();
    if (!message || disabled || uploadBlocked) return;
    onSend?.(message, {
      deepAnalysis: deepAnalysis || attachment !== undefined,
    });
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
        {attachment ? (
          <InputUploadStatus
            upload={attachment}
            refreshing={refreshingAttachment}
            onRefresh={onRefreshAttachment}
            onRemove={clearContext}
          />
        ) : (
          deepAnalysis && (
            <div className="composer__context">
              <BrainCircuitRegular aria-hidden="true" />
              <Text size={200}>Deep analysis enabled</Text>
              <Button
                appearance="subtle"
                aria-label="Use automatic mode"
                icon={<DismissRegular />}
                onClick={clearContext}
                size="small"
              />
            </div>
          )
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
            setDeepAnalysis(true);
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
              <MenuItem
                icon={<BrainCircuitRegular />}
                onClick={() => {
                  setDeepAnalysis(true);
                }}
              >
                Deep analysis
              </MenuItem>
            </MenuList>
          </MenuPopover>
        </Menu>
        <Tooltip
          content={running ? "Send to the running analysis" : "Send message"}
          relationship="label"
        >
          <Button
            appearance="primary"
            className="composer__command composer__send"
            icon={<SendRegular />}
            aria-label="Send message"
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
