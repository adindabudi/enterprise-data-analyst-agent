export type ApiProblem = {
  status: number;
  code: string;
  correlationId?: string;
};

export class ApiRequestError extends Error implements ApiProblem {
  readonly correlationId?: string;
  readonly body?: unknown;

  constructor(
    readonly status: number,
    readonly code: string,
    correlationId?: string,
    body?: unknown,
  ) {
    super(code);
    if (correlationId) this.correlationId = correlationId;
    if (body !== undefined) this.body = body;
  }
}

export class ApiClient {
  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      ...(signal ? { signal } : {}),
    });
    if (!response.ok) {
      const correlationId = response.headers.get("X-Correlation-ID");
      throw new ApiRequestError(
        response.status,
        "request_failed",
        correlationId ?? undefined,
        await response.json().catch(() => undefined),
      );
    }
    return (await response.json()) as T;
  }
}
