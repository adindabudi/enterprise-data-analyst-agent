import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Button,
  Link,
  Text,
  Tooltip,
} from "@fluentui/react-components";
import {
  ArrowClockwiseRegular,
  ArrowDownloadRegular,
  CheckmarkCircleRegular,
  CircleRegular,
  DocumentRegular,
  DocumentTableRegular,
  EyeRegular,
} from "@fluentui/react-icons";
import { useRef, useState } from "react";

import {
  artifactDownloadPath,
  deliverableArtifacts,
  type AgentTodoItem,
  type SessionTask,
  type SourceQuery,
} from "../api/analysis";
import { ArtifactPreviewDialog } from "../artifacts/ArtifactPreviewDialog";
import { checkpointActivity, type Activity } from "../chat/ActivityBlock";
import type { NarrativeMessage } from "../chat/Conversation";
import type { DataStep } from "../chat/DataSteps";
import { InputUploadStatus } from "../chat/InputUploadStatus";
import type { InputUpload } from "../chat/useAnalysisUpload";
import {
  fabricSourceName,
  ontologySourceStatus,
  type FabricSourceContext,
} from "../features/fabric/source-context";
import { QueryProvenance } from "./QueryProvenance";
import type { DetailLoadStatus, TaskArtifact } from "./useTaskDetails";

function LoadStatus({
  label,
  status,
  refresh,
}: {
  label: string;
  status: DetailLoadStatus;
  refresh: () => void;
}) {
  if (status === "loading")
    return (
      <Text role="status" size={200}>
        Loading {label.toLowerCase()}
      </Text>
    );
  if (status !== "error") return null;
  return (
    <div className="workspace-load-error">
      <Text role="alert" size={200}>
        {label} could not be refreshed.
      </Text>
      <Tooltip content={`Refresh ${label.toLowerCase()}`} relationship="label">
        <Button
          appearance="subtle"
          icon={<ArrowClockwiseRegular />}
          aria-label={`Refresh ${label.toLowerCase()}`}
          onClick={refresh}
        />
      </Tooltip>
    </div>
  );
}

export function WorkspaceDetails({
  artifacts,
  queries,
  tasks,
  todos,
  activities,
  messages,
  liveSteps,
  taskId,
  fabricSource,
  upload,
  artifactsStatus,
  provenanceStatus,
  refresh,
  onProvenanceToggle,
}: {
  artifacts: TaskArtifact[];
  queries: SourceQuery[];
  tasks: SessionTask[];
  todos: AgentTodoItem[];
  activities: Activity[];
  messages: NarrativeMessage[];
  liveSteps: DataStep[];
  taskId: string | null;
  fabricSource: FabricSourceContext;
  upload?: InputUpload | undefined;
  artifactsStatus: DetailLoadStatus;
  provenanceStatus: DetailLoadStatus;
  refresh: () => void;
  onProvenanceToggle: (open: boolean) => void;
}) {
  const [preview, setPreview] = useState<TaskArtifact>();
  const previewTrigger = useRef<HTMLButtonElement>(null);
  const outputs = deliverableArtifacts(artifacts, queries) as TaskArtifact[];
  const inputs = artifacts.filter((artifact) => artifact.kind === "input");
  const completed = todos.filter((todo) => todo.isComplete).length;
  const files = (items: TaskArtifact[]) =>
    items.map((artifact) => (
      <div
        className="workspace-file"
        key={`${artifact.taskId}-${artifact.artifactId}-${String(artifact.version)}`}
      >
        <span className="workspace-file__icon" aria-hidden="true">
          {artifact.kind === "xlsx" ? (
            <DocumentTableRegular />
          ) : (
            <DocumentRegular />
          )}
        </span>
        <div className="workspace-file__name">
          <Text title={artifact.displayName}>{artifact.displayName}</Text>
          <Text size={100}>
            {artifact.sizeBytes < 1024
              ? `${String(artifact.sizeBytes)} B`
              : `${(artifact.sizeBytes / 1024).toFixed(1)} KB`}
          </Text>
        </div>
        <Tooltip
          content={`Download ${artifact.displayName}`}
          relationship="label"
        >
          <Link
            className="workspace-file__command"
            download={artifact.displayName}
            aria-label={`Download ${artifact.displayName}`}
            href={artifactDownloadPath(artifact.taskId, artifact)}
          >
            <ArrowDownloadRegular />
          </Link>
        </Tooltip>
        {artifact.kind === "html" && (
          <Tooltip
            content={`Preview ${artifact.displayName}`}
            relationship="label"
          >
            <Button
              appearance="subtle"
              icon={<EyeRegular />}
              aria-label={`Preview ${artifact.displayName}`}
              onClick={(event) => {
                previewTrigger.current = event.currentTarget;
                setPreview(artifact);
              }}
            />
          </Tooltip>
        )}
      </div>
    ));

  return (
    <>
      <Accordion
        className="workspace-sections"
        multiple
        collapsible
        defaultOpenItems={["tasks", "inputs", "outputs"]}
        onToggle={(_, data) => {
          onProvenanceToggle(data.openItems.includes("provenance"));
        }}
      >
        <AccordionItem value="tasks">
          <AccordionHeader>
            Tasks
            {todos.length > 0 && (
              <span className="workspace-section__count">
                {completed} / {todos.length}
              </span>
            )}
          </AccordionHeader>
          <AccordionPanel role="region" aria-label="Tasks">
            {todos.length > 0 && (
              <ol className="workspace-steps" aria-label="Task steps">
                {todos.map((todo) => (
                  <li key={todo.id}>
                    <span aria-label={todo.isComplete ? "Completed" : "Open"}>
                      {todo.isComplete ? (
                        <CheckmarkCircleRegular />
                      ) : (
                        <CircleRegular />
                      )}
                    </span>
                    <div>
                      <Text>{todo.title}</Text>
                      {todo.description && (
                        <Text size={200}>{todo.description}</Text>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
            {tasks.length > 0 && (
              <ul className="workspace-runs" aria-label="Analysis runs">
                {tasks.map((task) => (
                  <li key={task.taskId}>
                    <Text>
                      {messages.find(
                        (message) => message.id === task.sourceMessageId,
                      )?.text ?? "Analysis"}
                    </Text>
                    <Text size={200}>
                      {checkpointActivity(task.taskId, task.status).label}
                    </Text>
                  </li>
                ))}
              </ul>
            )}
            {tasks.length === 0 && todos.length === 0 && (
              <Text size={200}>
                {activities.at(-1)?.label ?? "No tasks yet."}
              </Text>
            )}
          </AccordionPanel>
        </AccordionItem>
        <AccordionItem value="inputs">
          <AccordionHeader>
            Inputs
            {inputs.length > 0 && (
              <span className="workspace-section__count">{inputs.length}</span>
            )}
          </AccordionHeader>
          <AccordionPanel role="region" aria-label="Inputs">
            {fabricSource.availability !== "disabled" && (
              <div className="workspace-source">
                <Text weight="semibold">{fabricSourceName(fabricSource)}</Text>
                <Text size={200}>{ontologySourceStatus(fabricSource)}</Text>
              </div>
            )}
            {upload && <InputUploadStatus upload={upload} />}
            <LoadStatus
              label="Inputs"
              status={artifactsStatus}
              refresh={refresh}
            />
            {files(inputs)}
            {inputs.length === 0 && !upload && artifactsStatus === "ready" && (
              <Text size={200}>No uploaded inputs.</Text>
            )}
          </AccordionPanel>
        </AccordionItem>
        <AccordionItem value="outputs">
          <AccordionHeader>
            Outputs
            <span className="workspace-section__count">{outputs.length}</span>
          </AccordionHeader>
          <AccordionPanel role="region" aria-label="Outputs">
            <LoadStatus
              label="Outputs"
              status={artifactsStatus}
              refresh={refresh}
            />
            {files(outputs)}
            {outputs.length === 0 && artifactsStatus === "ready" && (
              <Text size={200}>No ready artifacts yet.</Text>
            )}
          </AccordionPanel>
        </AccordionItem>
        <AccordionItem value="provenance">
          <AccordionHeader>Provenance</AccordionHeader>
          <AccordionPanel role="region" aria-label="Provenance">
            <LoadStatus
              label="Provenance"
              status={provenanceStatus}
              refresh={refresh}
            />
            <QueryProvenance
              messages={messages}
              liveSteps={liveSteps}
              queries={queries}
              artifacts={artifacts}
              taskId={taskId}
              showEmpty={provenanceStatus === "ready"}
            />
          </AccordionPanel>
        </AccordionItem>
      </Accordion>
      {preview && (
        <ArtifactPreviewDialog
          key={`${preview.taskId}-${preview.artifactId}`}
          taskId={preview.taskId}
          artifact={preview}
          onClose={() => {
            setPreview(undefined);
            queueMicrotask(() => previewTrigger.current?.focus());
          }}
        />
      )}
    </>
  );
}
