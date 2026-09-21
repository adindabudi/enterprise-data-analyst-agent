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
  executedAt?: string;
  queryTruncated?: boolean;
};

const STATES = new Set(["running", "completed", "failed"]);

export function readDataStep(data: Record<string, string>): DataStep | null {
  const { stepId, kind, label, state, query, source, querySha256 } = data;
  if (!stepId || !label || typeof query !== "string" || !source || !querySha256)
    return null;
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
    ...(data.executedAt ? { executedAt: data.executedAt } : {}),
    ...(data.queryTruncated === "true" ? { queryTruncated: true } : {}),
  };
}

export function readStoredDataSteps(value: unknown): DataStep[] {
  if (value === undefined) return [];
  if (!Array.isArray(value))
    throw new Error("Saved query provenance is invalid");
  return value.map((entry: unknown) => {
    if (
      typeof entry !== "object" ||
      entry === null ||
      Array.isArray(entry) ||
      !Object.values(entry).every((item) => typeof item === "string")
    )
      throw new Error("Saved query provenance is invalid");
    const data = entry as Record<string, string>;
    const step = readDataStep(data);
    if (
      !step ||
      !/^[a-f0-9]{64}$/.test(step.querySha256) ||
      (step.resultSha256 !== undefined &&
        !/^[a-f0-9]{64}$/.test(step.resultSha256)) ||
      (data.rowCount !== undefined &&
        (!/^\d+$/.test(data.rowCount) ||
          !Number.isSafeInteger(Number(data.rowCount)))) ||
      (data.queryTruncated !== undefined &&
        !["true", "false"].includes(data.queryTruncated)) ||
      (step.state === "completed" && step.queryTruncated) ||
      (step.executedAt !== undefined &&
        !Number.isFinite(Date.parse(step.executedAt)))
    )
      throw new Error("Saved query provenance is invalid");
    return step;
  });
}

export function mergeDataSteps(...groups: DataStep[][]): DataStep[] {
  const steps = new Map<string, DataStep>();
  for (const group of groups) {
    for (const step of group) {
      const previous = steps.get(step.stepId);
      // A stale history snapshot must not replace a terminal live result with "running".
      if (!previous || previous.state === "running")
        steps.set(step.stepId, step);
    }
  }
  return [...steps.values()];
}
