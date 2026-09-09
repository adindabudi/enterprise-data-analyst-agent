import { Badge, Button, Spinner, Text } from "@fluentui/react-components";
import { ChevronDownRegular, ChevronRightRegular } from "@fluentui/react-icons";
import { useState } from "react";

export type DataStep = {
  stepId: string;
  kind: "gql" | "ontology_search";
  label: string;
  state: "running" | "completed" | "failed";
  query: string;
  source: string;
  rowCount?: number;
  querySha256: string;
  resultSha256?: string;
  detail?: string;
};

// Fabric writes and runs the ontology query itself, so naming ours would invent provenance.
const ONTOLOGY_NOTE =
  "Sent as a question. Fabric translates it internally, and the query it runs is not returned to this app.";

const STATES = new Set(["running", "completed", "failed"]);

export function readDataStep(data: Record<string, string>): DataStep | null {
  const { stepId, kind, label, state, query, source, querySha256 } = data;
  if (!stepId || !label || !query || !source || !querySha256) return null;
  if (kind !== "gql" && kind !== "ontology_search") return null;
  if (!STATES.has(state ?? "")) return null;
  const rowCount = Number(data.rowCount);
  return {
    stepId,
    kind,
    label,
    state: state as DataStep["state"],
    query,
    source,
    querySha256,
    ...(Number.isInteger(rowCount) && data.rowCount ? { rowCount } : {}),
    ...(data.resultSha256 ? { resultSha256: data.resultSha256 } : {}),
    ...(data.detail ? { detail: data.detail } : {}),
  };
}

function badgeColor(state: DataStep["state"]) {
  if (state === "failed") return "danger";
  return state === "completed" ? "success" : "informative";
}

function rowLabel(count: number): string {
  return `${String(count)} row${count === 1 ? "" : "s"}`;
}

function summaryLabel(count: number, running: boolean): string {
  if (running) return "Analyzing data";
  return `Analyzed · ${String(count)} data step${count === 1 ? "" : "s"}`;
}

function StepRow({ step }: { step: DataStep }) {
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? step.state === "failed";
  const meta = [
    step.rowCount === undefined ? null : rowLabel(step.rowCount),
    step.source,
    `query SHA-256 ${step.querySha256.slice(0, 12)}…`,
  ].filter((part): part is string => part !== null);
  return (
    <li className="data-step">
      <div className="data-step__summary">
        <Button
          appearance="subtle"
          aria-expanded={expanded}
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => {
            setOverride(!expanded);
          }}
        >
          {step.label}
        </Button>
        <div className="data-step__state">
          {step.state === "running" && (
            <Spinner aria-label="Step in progress" size="tiny" />
          )}
          <Badge appearance="tint" color={badgeColor(step.state)}>
            {step.state}
          </Badge>
        </div>
      </div>
      {expanded && (
        <div className="data-step__detail">
          <pre className="data-step__query">
            <code>{step.query}</code>
          </pre>
          {step.kind === "ontology_search" && (
            <Text size={100}>{ONTOLOGY_NOTE}</Text>
          )}
          <Text size={100}>{meta.join(" · ")}</Text>
          {step.detail && (
            <Text className="data-step__failure" size={200}>
              {step.detail}
            </Text>
          )}
        </div>
      )}
    </li>
  );
}

export function DataStepsBlock({
  steps,
  running,
}: {
  steps: DataStep[];
  running: boolean;
}) {
  const [override, setOverride] = useState<boolean | null>(null);
  if (steps.length === 0) return null;
  // A failure nobody can see is the one that gets reported as a number instead.
  const failed = steps.some((step) => step.state === "failed");
  const expanded = override ?? (running || failed);
  return (
    <section
      aria-label="Data steps"
      className={`data-steps${running ? " data-steps--running" : ""}`}
    >
      <div className="data-steps__summary">
        <Button
          appearance="subtle"
          aria-expanded={expanded}
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => {
            setOverride(!expanded);
          }}
        >
          {summaryLabel(steps.length, running)}
        </Button>
        {running && <Spinner aria-label="Reading the source" size="tiny" />}
      </div>
      {expanded && (
        <ol className="data-steps__list">
          {steps.map((step) => (
            <StepRow key={step.stepId} step={step} />
          ))}
        </ol>
      )}
    </section>
  );
}
