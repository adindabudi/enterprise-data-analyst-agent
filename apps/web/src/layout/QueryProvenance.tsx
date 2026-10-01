import { Link, Text } from "@fluentui/react-components";
import { ArrowDownloadRegular } from "@fluentui/react-icons";

import {
  artifactDownloadPath,
  queryRowsArtifact,
  type PublishedArtifact,
  type SourceQuery,
} from "../api/analysis";
import type { NarrativeMessage } from "../chat/Conversation";
import { mergeDataSteps, type DataStep } from "../chat/DataSteps";

type QueryGroup = {
  id: string;
  question: string;
  steps: DataStep[];
  queries: SourceQuery[];
};

export function QueryProvenance({
  messages,
  liveSteps,
  queries,
  artifacts,
  taskId,
  showEmpty = true,
}: {
  messages: NarrativeMessage[];
  liveSteps: DataStep[];
  queries: SourceQuery[];
  artifacts: PublishedArtifact[];
  taskId: string | null;
  showEmpty?: boolean;
}) {
  const groups = new Map<string, QueryGroup>();
  let current: QueryGroup | undefined;
  for (const message of messages) {
    if (message.role === "user") {
      current = {
        id: message.id,
        question: message.text,
        steps: [...(message.steps ?? [])],
        queries: [],
      };
      groups.set(message.id, current);
    } else if (current) {
      current.steps.push(...(message.steps ?? []));
    }
  }
  if (current) current.steps.push(...liveSteps);
  for (const group of groups.values()) {
    group.steps = mergeDataSteps(group.steps);
  }
  for (const query of queries) {
    const key = query.messageId ?? "unattributed";
    let group = groups.get(key);
    if (!group) {
      group = {
        id: key,
        question: "Question text unavailable",
        steps: [],
        queries: [],
      };
      groups.set(key, group);
    }
    group.queries.push(query);
  }
  const visibleGroups = [...groups.values()].filter(
    (group) => group.steps.length || group.queries.length,
  );
  if (visibleGroups.length === 0)
    return showEmpty ? (
      <Text>
        {messages.some((message) => message.role === "assistant")
          ? "No query records are available for this saved conversation."
          : "No queries yet."}
      </Text>
    ) : null;
  return (
    <>
      {visibleGroups.map((group) => (
        <details
          open
          role="group"
          aria-label={group.question}
          className="query-group"
          key={group.id}
        >
          <summary>{group.question}</summary>
          <div className="details-list">
            {group.steps
              .filter(
                (step) =>
                  !group.queries.some(
                    (query) =>
                      step.state === "completed" &&
                      step.querySha256 === query.querySha256 &&
                      step.resultSha256 === query.sha256,
                  ),
              )
              .map((step) => (
                <div className="details-artifact" key={step.stepId}>
                  <Text weight="semibold">
                    {step.state === "failed"
                      ? "Failed query"
                      : step.state === "running"
                        ? "Running query"
                        : step.rowCount === undefined
                          ? `Read data from ${step.source}`
                          : `Read ${String(step.rowCount)} row${step.rowCount === 1 ? "" : "s"} from ${step.source}`}
                  </Text>
                  <pre className="query-text">
                    {step.query || "(empty query request)"}
                  </pre>
                  {step.queryTruncated && (
                    <Text size={200}>
                      Request text is truncated for display; its hash identifies
                      the full submitted request.
                    </Text>
                  )}
                  {step.executedAt && (
                    <Text size={200}>
                      {new Date(step.executedAt).toLocaleString()}
                    </Text>
                  )}
                  {step.kind === "ontology_search" && (
                    <Text size={200}>
                      Fabric translates this question; its generated query is
                      not returned.
                    </Text>
                  )}
                  {step.detail && <Text size={200}>{step.detail}</Text>}
                  <Text size={200}>
                    query SHA-256 {step.querySha256.slice(0, 12)}
                    {"\u2026"}
                  </Text>
                  {step.resultSha256 && (
                    <Text size={200}>
                      rows SHA-256 {step.resultSha256.slice(0, 12)}
                      {"\u2026"}
                    </Text>
                  )}
                </div>
              ))}
            {group.queries.map((query) => {
              const rows = queryRowsArtifact(query, artifacts);
              return (
                <div
                  className="details-artifact"
                  key={`${query.artifactId}-${String(query.version)}`}
                >
                  <Text weight="semibold">
                    Read {query.rowCount} row{query.rowCount === 1 ? "" : "s"}
                    {query.sourceAlias ? ` from ${query.sourceAlias}` : ""}
                  </Text>
                  <pre className="query-text">{query.query}</pre>
                  <Text size={200}>
                    {new Date(query.executedAt).toLocaleString()}
                  </Text>
                  <Text size={200}>
                    query SHA-256 {query.querySha256.slice(0, 12)}
                    {"\u2026"}
                  </Text>
                  <Text size={200}>
                    rows SHA-256 {query.sha256.slice(0, 12)}
                    {"\u2026"}
                  </Text>
                  {(query.taskId ?? taskId) && rows && (
                    <Link
                      download={rows.displayName}
                      href={artifactDownloadPath(
                        query.taskId ?? taskId ?? "",
                        rows,
                      )}
                    >
                      <ArrowDownloadRegular /> {rows.displayName}
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      ))}
    </>
  );
}
