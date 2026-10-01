import type { TaskSummary } from "@eda/contracts";
import { readStoredDataSteps, type DataStep } from "../chat/data-step";

export type AnalysisIdentity = {
  sessionId: string;
  messageId: string;
  taskId: string;
};

type SessionMessageIdentity = {
  sessionId: string;
  messageId: string;
};

export type FinalAnalysisMessage = {
  messageId: string;
  role: "assistant";
  text: string;
};

export type ChatHistoryMessage = {
  role: "user" | "assistant";
  text: string;
};

export type PublishedArtifact = {
  artifactId: string;
  version: number;
  kind: string;
  sha256: string;
  displayName: string;
  sizeBytes: number;
};

export type AgentTodoItem = {
  id: number;
  title: string;
  description: string | null;
  isComplete: boolean;
};

export type AnalysisUpload = {
  uploadId: string;
  displayName: string;
  state: "scanning" | "clean" | "rejected" | "scan_failed";
};

function readUpload(value: unknown): AnalysisUpload {
  if (
    !isRecord(value) ||
    typeof value.uploadId !== "string" ||
    !/^upl_[A-Za-z0-9_-]{8,}$/.test(value.uploadId) ||
    typeof value.displayName !== "string" ||
    !value.displayName ||
    !["scanning", "clean", "rejected", "scan_failed"].includes(
      String(value.state),
    )
  ) {
    throw new Error("Upload response is invalid");
  }
  return value as AnalysisUpload;
}

export async function uploadAnalysisInput(
  sessionId: string,
  file: File,
): Promise<AnalysisUpload> {
  if (!SESSION_PATTERN.test(sessionId))
    throw new Error("Session ID is invalid");
  const headers = new Headers(writeHeaders());
  headers.delete("Content-Type");
  const body = new FormData();
  body.append("upload", file);
  return readUpload(
    await requestJson(
      `/api/sessions/${encodeURIComponent(sessionId)}/uploads`,
      {
        method: "POST",
        headers,
        body,
      },
    ),
  );
}

export async function readUploadStatus(
  sessionId: string,
  uploadId: string,
): Promise<AnalysisUpload> {
  if (
    !SESSION_PATTERN.test(sessionId) ||
    !/^upl_[A-Za-z0-9_-]{8,}$/.test(uploadId)
  ) {
    throw new Error("Upload identity is invalid");
  }
  return readUpload(
    await requestJson(
      `/api/sessions/${encodeURIComponent(sessionId)}/uploads/${encodeURIComponent(uploadId)}`,
      {
        headers: { Accept: "application/json" },
      },
    ),
  );
}

export async function createAnalysisSession(title: string): Promise<string> {
  const session = await requestJson("/api/sessions", {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ title }),
  });
  return readIdentifier(session, "sessionId", SESSION_PATTERN);
}

const SESSION_PATTERN = /^ses_[A-Za-z0-9_-]{8,}$/;
const MESSAGE_PATTERN = /^msg_[A-Za-z0-9_-]{8,}$/;
const TASK_PATTERN = /^task_[A-Za-z0-9_-]{8,}$/;
const ARTIFACT_PATTERN = /^artifact-[A-Za-z0-9_-]{8,}$/;
const DIGEST_PATTERN = /^[a-f0-9]{64}$/;
const IDEMPOTENCY_PATTERN = /^[!-~]{8,128}$/;
const MAX_CONTEXT_MESSAGES = 10;
const MAX_CONTEXT_MESSAGE_CHARS = 4_000;
const MAX_CONTEXT_CHARS = 12_000;

export type AnalysisSession = {
  sessionId: string;
  title: string;
  lastActivityAt: string;
};

export type SessionMessage = {
  messageId: string;
  role: "user" | "assistant";
  text: string;
  createdAt: string;
  taskId: string | null;
  steps?: DataStep[];
};

export type SessionTask = {
  taskId: string;
  status: TaskSummary["status"];
  sourceMessageId: string | null;
  finalMessageId: string | null;
  createdAt: string;
  updatedAt: string;
};

export type AnalysisHistory = {
  messages: SessionMessage[];
  tasks: SessionTask[];
};

const TASK_STATUSES = new Set([
  "planning",
  "acquiring_data",
  "analyzing",
  "generating",
  "validating",
  "publishing",
  "blocked_auth",
  "cancelling",
  "completed",
  "cancelled",
  "failed",
  "failed_cancellation",
]);

export function taskIsRunning(status: TaskSummary["status"]): boolean {
  return !["completed", "cancelled", "failed", "failed_cancellation"].includes(
    status,
  );
}

function readSession(value: unknown): AnalysisSession {
  if (
    !isRecord(value) ||
    typeof value.title !== "string" ||
    typeof value.lastActivityAt !== "string"
  )
    throw new Error("Session response is invalid");
  return {
    sessionId: readIdentifier(value, "sessionId", SESSION_PATTERN),
    title: value.title,
    lastActivityAt: value.lastActivityAt,
  };
}

export async function listAnalysisSessions(): Promise<AnalysisSession[]> {
  const value = await requestJson("/api/sessions", {
    credentials: "same-origin",
  });
  if (!Array.isArray(value)) throw new Error("Session list is invalid");
  return (value as unknown[]).map(readSession);
}

export async function readAnalysisSession(
  sessionId: string,
): Promise<AnalysisSession> {
  if (!SESSION_PATTERN.test(sessionId))
    throw new Error("Session ID is invalid");
  const session = readSession(
    await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      credentials: "same-origin",
    }),
  );
  if (session.sessionId !== sessionId)
    throw new Error("Session identity does not match");
  return session;
}

export async function readAnalysisHistory(
  sessionId: string,
): Promise<AnalysisHistory> {
  if (!SESSION_PATTERN.test(sessionId))
    throw new Error("Session ID is invalid");
  const value = await requestJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/history`,
    { credentials: "same-origin" },
  );
  if (
    !isRecord(value) ||
    !Array.isArray(value.messages) ||
    !Array.isArray(value.tasks)
  )
    throw new Error("Session history is invalid");
  for (const message of value.messages as unknown[]) {
    if (
      !isRecord(message) ||
      !["user", "assistant"].includes(String(message.role)) ||
      typeof message.text !== "string" ||
      typeof message.createdAt !== "string"
    )
      throw new Error("Session message is invalid");
    readIdentifier(message, "messageId", MESSAGE_PATTERN);
    message.steps = readStoredDataSteps(message.steps);
  }
  for (const task of value.tasks as unknown[]) {
    if (
      !isRecord(task) ||
      !TASK_STATUSES.has(String(task.status)) ||
      typeof task.createdAt !== "string" ||
      typeof task.updatedAt !== "string"
    )
      throw new Error("Session task is invalid");
    readIdentifier(task, "taskId", TASK_PATTERN);
  }
  return value as AnalysisHistory;
}

export type AnalysisTaskSummary = Pick<
  TaskSummary,
  "taskId" | "sessionId" | "status"
> & { finalMessageId?: string | null };

export async function readAnalysisTask(
  taskId: string,
): Promise<AnalysisTaskSummary> {
  validateTask(taskId);
  const value = await requestJson(`/api/tasks/${encodeURIComponent(taskId)}`, {
    credentials: "same-origin",
  });
  if (
    !isRecord(value) ||
    value.taskId !== taskId ||
    !TASK_STATUSES.has(String(value.status))
  )
    throw new Error("Task response is invalid");
  readIdentifier(value, "sessionId", SESSION_PATTERN);
  return value as AnalysisTaskSummary;
}

export async function createAnalysis(
  title: string,
  text: string,
  requestId: string,
  history: ChatHistoryMessage[],
  activeSessionId?: string,
  inputUploadIds: string[] = [],
): Promise<AnalysisIdentity> {
  validateIdempotency(requestId);
  const { sessionId, messageId } = await createSessionMessage(
    title,
    text,
    requestId,
    activeSessionId,
  );
  const task = await requestJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/tasks`,
    {
      method: "POST",
      headers: writeHeaders(`${requestId}-task`),
      body: JSON.stringify({
        messageId,
        history: compactChatHistory(history),
        ...(inputUploadIds.length ? { inputUploadIds } : {}),
      }),
    },
  );
  const taskId = readIdentifier(task, "taskId", TASK_PATTERN);
  return { sessionId, messageId, taskId };
}

function compactChatHistory(
  history: ChatHistoryMessage[],
): ChatHistoryMessage[] {
  const compacted: ChatHistoryMessage[] = [];
  let remainingCharacters = MAX_CONTEXT_CHARS;
  const firstIncludedIndex = Math.max(0, history.length - MAX_CONTEXT_MESSAGES);
  for (
    let index = history.length - 1;
    index >= firstIncludedIndex;
    index -= 1
  ) {
    const message = history[index];
    if (!message || !message.text.trim()) {
      throw new Error("Interactive chat history is invalid");
    }
    const text = message.text
      .trim()
      .slice(0, Math.min(MAX_CONTEXT_MESSAGE_CHARS, remainingCharacters));
    if (!text) break;
    compacted.unshift({ role: message.role, text });
    remainingCharacters -= text.length;
    if (remainingCharacters === 0) break;
  }
  return compacted;
}

export async function steerAnalysis(
  taskId: string,
  instruction: string,
  requestId: string,
): Promise<void> {
  validateTask(taskId);
  validateIdempotency(requestId);
  if (!instruction.trim() || instruction.length > 4_000)
    throw new Error("Steering instruction is invalid");
  await requestJson(`/api/tasks/${encodeURIComponent(taskId)}/steer`, {
    method: "POST",
    headers: writeHeaders(requestId),
    body: JSON.stringify({ instruction }),
  });
}

export async function cancelAnalysis(
  taskId: string,
  requestId: string,
): Promise<void> {
  validateTask(taskId);
  validateIdempotency(requestId);
  await requestJson(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
    headers: writeHeaders(requestId),
  });
}

export async function getFinalMessage(
  taskId: string,
  messageId: string,
): Promise<FinalAnalysisMessage> {
  validateTask(taskId);
  if (!MESSAGE_PATTERN.test(messageId))
    throw new Error("Final message ID is invalid");
  const value = await requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/messages/${encodeURIComponent(messageId)}`,
    { credentials: "same-origin", headers: { Accept: "application/json" } },
  );
  if (
    !isRecord(value) ||
    Object.keys(value).some(
      (key) => !["messageId", "role", "text"].includes(key),
    ) ||
    value.messageId !== messageId ||
    value.role !== "assistant" ||
    typeof value.text !== "string" ||
    !value.text
  ) {
    throw new Error("Final message response is invalid");
  }
  return value as FinalAnalysisMessage;
}

export interface SourceQuery {
  taskId?: string;
  artifactId: string;
  version: number;
  sha256: string;
  displayName: string;
  query: string;
  querySha256: string;
  rowCount: number;
  sourceAlias: string | null;
  /** The chat turn that asked. Null on rows recorded before this was tracked. */
  messageId: string | null;
  executedAt: string;
}

export interface TaskProvenance {
  sourceQueries: SourceQuery[];
  artifacts: PublishedArtifact[];
}

export async function readTaskProvenance(
  taskId: string,
): Promise<TaskProvenance> {
  validateTask(taskId);
  const value = await requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/provenance`,
    { credentials: "same-origin", headers: { Accept: "application/json" } },
  );
  if (
    !isRecord(value) ||
    !Array.isArray(value.sourceQueries) ||
    !Array.isArray(value.artifacts)
  ) {
    throw new Error("Provenance response is invalid");
  }
  const sourceQueries = value.sourceQueries.map((entry) => {
    if (
      !isRecord(entry) ||
      typeof entry.artifactId !== "string" ||
      !ARTIFACT_PATTERN.test(entry.artifactId) ||
      typeof entry.sha256 !== "string" ||
      !DIGEST_PATTERN.test(entry.sha256) ||
      typeof entry.querySha256 !== "string" ||
      !DIGEST_PATTERN.test(entry.querySha256) ||
      typeof entry.query !== "string" ||
      !entry.query ||
      typeof entry.displayName !== "string" ||
      typeof entry.rowCount !== "number" ||
      entry.rowCount < 0 ||
      typeof entry.executedAt !== "string"
    ) {
      throw new Error("Provenance entry is invalid");
    }
    return {
      artifactId: entry.artifactId,
      version: typeof entry.version === "number" ? entry.version : 1,
      sha256: entry.sha256,
      displayName: entry.displayName,
      query: entry.query,
      querySha256: entry.querySha256,
      rowCount: entry.rowCount,
      sourceAlias:
        typeof entry.sourceAlias === "string" ? entry.sourceAlias : null,
      messageId: typeof entry.messageId === "string" ? entry.messageId : null,
      executedAt: entry.executedAt,
    };
  });
  return { sourceQueries, artifacts: await listTaskArtifacts(taskId) };
}

export async function listTaskArtifacts(
  taskId: string,
): Promise<PublishedArtifact[]> {
  validateTask(taskId);
  const value = await requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/artifacts`,
    { credentials: "same-origin", headers: { Accept: "application/json" } },
  );
  if (!isRecord(value) || !Array.isArray(value.artifacts)) {
    throw new Error("Artifact list response is invalid");
  }
  return value.artifacts.map((entry) => {
    if (
      !isRecord(entry) ||
      typeof entry.artifactId !== "string" ||
      !ARTIFACT_PATTERN.test(entry.artifactId) ||
      typeof entry.version !== "number" ||
      !Number.isInteger(entry.version) ||
      entry.version < 1 ||
      typeof entry.kind !== "string" ||
      typeof entry.sha256 !== "string" ||
      !DIGEST_PATTERN.test(entry.sha256) ||
      typeof entry.displayName !== "string" ||
      !entry.displayName ||
      typeof entry.sizeBytes !== "number" ||
      entry.sizeBytes < 0
    ) {
      throw new Error("Artifact list entry is invalid");
    }
    return {
      artifactId: entry.artifactId,
      version: entry.version,
      kind: entry.kind,
      sha256: entry.sha256,
      displayName: entry.displayName,
      sizeBytes: entry.sizeBytes,
    };
  });
}

export function artifactDownloadPath(
  taskId: string,
  artifact: PublishedArtifact,
): string {
  validateTask(taskId);
  if (!ARTIFACT_PATTERN.test(artifact.artifactId))
    throw new Error("Artifact ID is invalid");
  return `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifact.artifactId)}/versions/${String(artifact.version)}/content`;
}

// Every read of the source publishes its rows as an artifact. Listing those beside the workbook
// buries the thing the reader asked for, so they belong with the query that produced them.
export function isQueryRows(
  artifact: PublishedArtifact,
  sourceQueries: SourceQuery[],
): boolean {
  return sourceQueries.some(
    (query) => query.artifactId === artifact.artifactId,
  );
}

export function deliverableArtifacts(
  artifacts: PublishedArtifact[],
  sourceQueries: SourceQuery[],
): PublishedArtifact[] {
  const outputKinds = new Set([
    "html",
    "xlsx",
    "xlsm",
    "pptx",
    "docx",
    "pdf",
    "svg",
    "png",
    "mmd",
  ]);
  return artifacts.filter(
    (artifact) =>
      outputKinds.has(artifact.kind) && !isQueryRows(artifact, sourceQueries),
  );
}

export function queryRowsArtifact(
  query: SourceQuery,
  artifacts: PublishedArtifact[],
): PublishedArtifact | undefined {
  return artifacts.find(
    (artifact) =>
      artifact.artifactId === query.artifactId &&
      artifact.version === query.version,
  );
}

export async function listSessionTodos(
  sessionId: string,
): Promise<AgentTodoItem[]> {
  if (!SESSION_PATTERN.test(sessionId))
    throw new Error("Session ID is invalid");
  const value = await requestJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/todos`,
    { credentials: "same-origin", headers: { Accept: "application/json" } },
  );
  if (!isRecord(value) || !Array.isArray(value.items)) {
    throw new Error("Todo list response is invalid");
  }
  return value.items.map((entry) => {
    if (
      !isRecord(entry) ||
      typeof entry.id !== "number" ||
      !Number.isInteger(entry.id) ||
      typeof entry.title !== "string" ||
      !entry.title ||
      typeof entry.isComplete !== "boolean" ||
      (entry.description !== null && typeof entry.description !== "string")
    ) {
      throw new Error("Todo list entry is invalid");
    }
    return {
      id: entry.id,
      title: entry.title,
      description: entry.description,
      isComplete: entry.isComplete,
    };
  });
}

export class AnalysisRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AnalysisRequestError";
  }
}

async function requestJson(path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) {
    throw new AnalysisRequestError("Analysis request failed", response.status);
  }
  return response.json();
}

async function createSessionMessage(
  title: string,
  text: string,
  requestId: string,
  activeSessionId?: string,
): Promise<SessionMessageIdentity> {
  validateMessageInput(title, text);
  let sessionId = activeSessionId;
  if (sessionId === undefined) {
    const session = await requestJson("/api/sessions", {
      method: "POST",
      headers: writeHeaders(),
      body: JSON.stringify({ title }),
    });
    sessionId = readIdentifier(session, "sessionId", SESSION_PATTERN);
  } else if (!SESSION_PATTERN.test(sessionId)) {
    throw new Error("Active session ID is invalid");
  }
  const message = await requestJson(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      headers: writeHeaders(`${requestId}-message`),
      body: JSON.stringify({ text }),
    },
  );
  return {
    sessionId,
    messageId: readIdentifier(message, "messageId", MESSAGE_PATTERN),
  };
}

function validateMessageInput(title: string, text: string): void {
  if (
    !title.trim() ||
    title.length > 120 ||
    !text.trim() ||
    text.length > 1_000_000
  ) {
    throw new Error("Analysis request is invalid");
  }
}

function writeHeaders(idempotencyKey?: string): HeadersInit {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken(),
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  };
}

function csrfToken(): string {
  for (const part of document.cookie.split(";")) {
    const [rawName, ...rawValue] = part.trim().split("=");
    if (rawName === "eda_csrf") {
      const value = decodeURIComponent(rawValue.join("="));
      if (value) return value;
    }
  }
  throw new Error("CSRF token is unavailable");
}

function readIdentifier(value: unknown, key: string, pattern: RegExp): string {
  if (
    !isRecord(value) ||
    typeof value[key] !== "string" ||
    !pattern.test(value[key])
  ) {
    throw new Error("Analysis response is invalid");
  }
  return value[key];
}

function validateTask(taskId: string): void {
  if (!TASK_PATTERN.test(taskId)) throw new Error("Task ID is invalid");
}

function validateIdempotency(value: string): void {
  if (!IDEMPOTENCY_PATTERN.test(value) || value.length > 96) {
    throw new Error("Idempotency key is invalid");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
