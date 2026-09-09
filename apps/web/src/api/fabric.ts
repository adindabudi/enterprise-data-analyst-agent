const FABRIC_START_PATH = "/api/fabric/auth/start";
const FABRIC_COMPLETE_PATH = "/api/fabric/auth/complete";
const FABRIC_STATUS_PATH = "/api/fabric/auth/status";

export type FabricSourceMetadata = {
  alias: string;
  description: string;
};

export type FabricAuthorizationStatus = {
  provider: "semantic_model" | "ontology";
  state: "unlinked" | "linked" | "reauth_required";
  chatQuery: boolean;
  source?: FabricSourceMetadata;
};

export async function startFabricAuthorization(
  taskId?: string,
): Promise<string> {
  if (taskId !== undefined && !/^task_[A-Za-z0-9_-]{8,}$/.test(taskId)) {
    throw new Error("task ID is invalid");
  }
  const response = await fetch(FABRIC_START_PATH, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken(),
    },
    body: JSON.stringify(taskId === undefined ? {} : { taskId }),
  });
  if (!response.ok) {
    throw new Error("Fabric authorization could not start");
  }
  const payload: unknown = await response.json();
  if (!isRecord(payload) || Object.keys(payload).length !== 1) {
    throw new Error("Fabric authorization response is invalid");
  }
  const authorizationUrl = payload.authorizationUrl;
  if (typeof authorizationUrl !== "string") {
    throw new Error("Fabric authorization response is invalid");
  }
  const parsed = new URL(authorizationUrl);
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname !== "login.microsoftonline.com" ||
    !/^\/[0-9a-f-]{36}\/oauth2\/v2\.0\/authorize$/i.test(parsed.pathname)
  ) {
    throw new Error("Fabric authorization URL is invalid");
  }
  return parsed.href;
}

export async function getFabricAuthorizationStatus(): Promise<FabricAuthorizationStatus> {
  const response = await fetch(FABRIC_STATUS_PATH, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error("Fabric authorization status is unavailable");
  }
  const payload: unknown = await response.json();
  if (
    !isRecord(payload) ||
    Object.keys(payload).some(
      (key) => !["provider", "state", "chatQuery", "source"].includes(key),
    ) ||
    !["semantic_model", "ontology"].includes(String(payload.provider)) ||
    !["unlinked", "linked", "reauth_required"].includes(
      String(payload.state),
    ) ||
    typeof payload.chatQuery !== "boolean" ||
    (payload.source !== undefined && !isSourceMetadata(payload.source))
  ) {
    throw new Error("Fabric authorization status is invalid");
  }
  return payload as FabricAuthorizationStatus;
}

export async function completeFabricAuthorization(): Promise<void> {
  const response = await fetch(FABRIC_COMPLETE_PATH, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrfToken() },
  });
  if (!response.ok) {
    throw new Error("Fabric authorization could not complete");
  }
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSourceMetadata(value: unknown): value is FabricSourceMetadata {
  return (
    isRecord(value) &&
    Object.keys(value).every((key) => ["alias", "description"].includes(key)) &&
    typeof value.alias === "string" &&
    /^[a-z][a-z0-9-]{1,39}$/.test(value.alias) &&
    typeof value.description === "string" &&
    value.description.trim().length > 0 &&
    Array.from(value.description).length <= 240
  );
}
