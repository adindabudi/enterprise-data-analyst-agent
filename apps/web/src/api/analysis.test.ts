import { afterEach, describe, expect, it, vi } from "vitest";

import {
  artifactDownloadPath,
  cancelAnalysis,
  deliverableArtifacts,
  queryRowsArtifact,
  createAnalysis,
  getFinalMessage,
  listSessionTodos,
  listTaskArtifacts,
  readTaskProvenance,
  streamInteractiveChat,
  steerAnalysis,
  uploadAnalysisInput,
  readUploadStatus,
  type ChatHistoryMessage,
} from "./analysis";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "eda_csrf=; Max-Age=0; Path=/";
});

describe("analysis API", () => {
  it("uploads real bytes with CSRF and checks the authoritative scan state", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            uploadId: "upl_input_12345678",
            displayName: "revenue.csv",
            state: "scanning",
          },
          202,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            uploadId: "upl_input_12345678",
            displayName: "revenue.csv",
            state: "clean",
            sizeBytes: 5,
            sha256: "a".repeat(64),
          },
          200,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["a,b\n1"], "revenue.csv", { type: "text/csv" });
    const upload = await uploadAnalysisInput("ses_analysis_12345678", file);
    expect(upload.state).toBe("scanning");
    const body = fetchMock.mock.calls[0]?.[1]?.body as FormData;
    expect(body.get("upload")).toBe(file);
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-value");
    expect(headers.has("Content-Type")).toBe(false);
    expect(
      (await readUploadStatus("ses_analysis_12345678", upload.uploadId)).state,
    ).toBe("clean");
  });

  it("creates a session, canonical message, and durable task", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_analysis_12345678", title: "Revenue review" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_analysis_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            taskId: "task_analysis_12345678",
            sessionId: "ses_analysis_12345678",
            status: "planning",
            checkpointSequence: 0,
            activeAttemptId: null,
            finalMessageId: null,
          },
          202,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createAnalysis(
      "Revenue review",
      "Compare APAC revenue",
      "request-12345678",
      [
        { role: "user", text: "Which ICUs are above 75%?" },
        { role: "assistant", text: "Four ICUs, 38 of 308 patients." },
      ],
    );

    expect(result).toEqual({
      sessionId: "ses_analysis_12345678",
      messageId: "msg_analysis_12345678",
      taskId: "task_analysis_12345678",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "Revenue review" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/sessions/ses_analysis_12345678/messages",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: "Compare APAC revenue" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/sessions/ses_analysis_12345678/tasks",
      expect.objectContaining({
        method: "POST",
        // Without the conversation the worker plans against a sentence it cannot resolve.
        body: JSON.stringify({
          messageId: "msg_analysis_12345678",
          history: [
            { role: "user", text: "Which ICUs are above 75%?" },
            { role: "assistant", text: "Four ICUs, 38 of 308 patients." },
          ],
        }),
      }),
    );
    for (const [, init] of fetchMock.mock.calls) {
      const headers = new Headers(init?.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-value");
    }
  });

  it("binds uploaded inputs to the task request", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_input_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        jsonResponse({ taskId: "task_input_12345678" }, 202),
      );
    vi.stubGlobal("fetch", fetchMock);
    await createAnalysis(
      "Uploaded analysis",
      "Analyze this",
      "request-input-12345678",
      [],
      "ses_analysis_12345678",
      ["upl_input_12345678"],
    );
    const body = fetchMock.mock.calls[1]?.[1]?.body;
    if (typeof body !== "string")
      throw new Error("Expected a JSON task request");
    expect(JSON.parse(body)).toMatchObject({
      inputUploadIds: ["upl_input_12345678"],
    });
  });

  it("sends steer and cancel controls with distinct idempotency keys", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ commandId: "cmd_steer_12345678", sequence: 1 }, 202),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            commandId: "cmd_cancel_12345678",
            sequence: 2,
            cancellationRequested: true,
          },
          202,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await steerAnalysis(
      "task_analysis_12345678",
      "Focus on APAC",
      "steer-request-12345678",
    );
    await cancelAnalysis("task_analysis_12345678", "cancel-request-12345678");

    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Idempotency-Key"),
    ).toBe("steer-request-12345678");
    expect(
      new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("Idempotency-Key"),
    ).toBe("cancel-request-12345678");
  });

  it("delivers the data steps a turn reports so the answer can be traced", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const step = {
      stepId: "step-1",
      kind: "gql",
      label: "Queried lamna-healthcare graph",
      state: "completed",
      query: "MATCH (r:rooms) RETURN count(*) AS total",
      source: "lamna-healthcare",
      rowCount: "1",
      querySha256: "a".repeat(64),
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_chat_12345678", title: "Private chat" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_chat_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          [
            `event: data_step\ndata: ${JSON.stringify(step)}\n\n`,
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          ].join(""),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const updates: Array<{ event: string; data: Record<string, string> }> = [];

    await streamInteractiveChat(
      "Private chat",
      "How many rooms?",
      [],
      "request-chat-12345679",
      new AbortController().signal,
      (update) => updates.push(update),
    );

    expect(updates[0]).toEqual({ event: "data_step", data: step });
  });

  it("streams interactive chat without creating a durable task", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_chat_12345678", title: "Private chat" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_chat_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          [
            'event: status\ndata: {"message":"Agent is thinking"}\n\n',
            'event: delta\ndata: {"text":"Hello"}\n\n',
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          ].join(""),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const updates: Array<{ event: string; data: Record<string, string> }> = [];

    const identity = await streamInteractiveChat(
      "Private chat",
      "Say hello",
      [{ role: "assistant", text: "Prior answer" }],
      "request-chat-12345678",
      new AbortController().signal,
      (update) => updates.push(update),
    );

    expect(identity).toEqual({
      sessionId: "ses_chat_12345678",
      messageId: "msg_chat_12345678",
    });
    expect(updates.map((update) => update.event)).toEqual([
      "status",
      "delta",
      "completed",
    ]);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/sessions",
      "/api/sessions/ses_chat_12345678/messages",
      "/api/sessions/ses_chat_12345678/chat",
    ]);
    expect(
      fetchMock.mock.calls.some(([path]) =>
        requestUrl(path).includes("/tasks"),
      ),
    ).toBe(false);
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/sessions/ses_chat_12345678/chat",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          messageId: "msg_chat_12345678",
          history: [{ role: "assistant", text: "Prior answer" }],
        }),
      }),
    );
  });

  it("appends follow-up turns to the active session instead of creating another thread", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_followup_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: delta\ndata: {"text":"Follow-up response."}\n\n' +
            'event: completed\ndata: {"messageId":"msg_reply_87654321"}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const identity = await streamInteractiveChat(
      "Private chat",
      "Continue that answer",
      [{ role: "assistant", text: "Prior answer" }],
      "request-followup-12345678",
      new AbortController().signal,
      vi.fn(),
      "ses_existing_12345678",
    );

    expect(identity.sessionId).toBe("ses_existing_12345678");
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/sessions/ses_existing_12345678/messages",
      "/api/sessions/ses_existing_12345678/chat",
    ]);
  });

  it("rejects an interactive stream that closes without a terminal event", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_chat_12345678", title: "Private chat" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_chat_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: status\ndata: {"message":"Agent is thinking"}\n\n',
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      streamInteractiveChat(
        "Private chat",
        "Say hello",
        [],
        "request-chat-12345678",
        new AbortController().signal,
        vi.fn(),
      ),
    ).rejects.toThrow("ended without a terminal event");
  });

  it("sends only the five most recent conversation turns for coreference", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_chat_12345678", title: "Private chat" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_chat_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const history = Array.from({ length: 14 }, (_, index) => ({
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
      text: `message-${String(index)}`,
    }));

    await streamInteractiveChat(
      "Private chat",
      "What about those?",
      history,
      "request-chat-12345678",
      new AbortController().signal,
      vi.fn(),
    );

    const body = chatRequestBody(fetchMock.mock.calls[2]?.[1]?.body);
    expect(body.history).toHaveLength(10);
    expect(body.history.at(0)?.text).toBe("message-4");
    expect(body.history.at(9)?.text).toBe("message-13");
  });

  it("caps recent coreference context by message and total characters", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { sessionId: "ses_chat_12345678", title: "Private chat" },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messageId: "msg_chat_12345678" }, 201),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const history = Array.from({ length: 5 }, (_, index) => ({
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
      text: String(index).repeat(5_000),
    }));

    await streamInteractiveChat(
      "Private chat",
      "What about those?",
      history,
      "request-chat-12345678",
      new AbortController().signal,
      vi.fn(),
    );

    const body = chatRequestBody(fetchMock.mock.calls[2]?.[1]?.body);
    expect(body.history).toHaveLength(3);
    expect(body.history.map((message) => message.text[0])).toEqual([
      "2",
      "3",
      "4",
    ]);
    expect(body.history.every((message) => message.text.length === 4_000)).toBe(
      true,
    );
  });

  it("loads only the task-bound canonical final message", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          messageId: "msg_final_12345678",
          role: "assistant",
          text: "Validated answer.",
        },
        200,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const message = await getFinalMessage(
      "task_analysis_12345678",
      "msg_final_12345678",
    );

    expect(message.text).toBe("Validated answer.");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/task_analysis_12345678/messages/msg_final_12345678",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("lists published artifacts for a task", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          artifacts: [
            {
              artifactId: "artifact-abcdefgh12345678",
              version: 2,
              kind: "xlsx",
              sha256: "a".repeat(64),
              displayName: "analysis.xlsx",
              sizeBytes: 2048,
            },
          ],
        },
        200,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const artifacts = await listTaskArtifacts("task_analysis_12345678");

    expect(artifacts).toEqual([
      {
        artifactId: "artifact-abcdefgh12345678",
        version: 2,
        kind: "xlsx",
        sha256: "a".repeat(64),
        displayName: "analysis.xlsx",
        sizeBytes: 2048,
      },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/tasks/task_analysis_12345678/artifacts",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("rejects an artifact entry that does not match the contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          jsonResponse(
            { artifacts: [{ artifactId: "nope", version: 1 }] },
            200,
          ),
        ),
    );

    await expect(listTaskArtifacts("task_analysis_12345678")).rejects.toThrow(
      "Artifact list entry is invalid",
    );
  });

  it("builds an owner-scoped artifact download path", () => {
    const path = artifactDownloadPath("task_analysis_12345678", {
      artifactId: "artifact-abcdefgh12345678",
      version: 2,
      kind: "xlsx",
      sha256: "a".repeat(64),
      displayName: "analysis.xlsx",
      sizeBytes: 1,
    });

    expect(path).toBe(
      "/api/tasks/task_analysis_12345678/artifacts/artifact-abcdefgh12345678/versions/2/content",
    );
  });

  it("lists the agent todo items for a session", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          items: [
            {
              id: 1,
              title: "Reconcile the control total",
              description: null,
              isComplete: true,
            },
          ],
        },
        200,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const items = await listSessionTodos("ses_analysis_12345678");

    expect(items).toEqual([
      {
        id: 1,
        title: "Reconcile the control total",
        description: null,
        isComplete: true,
      },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/sessions/ses_analysis_12345678/todos",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("rejects a todo entry that does not match the contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(jsonResponse({ items: [{ id: 1 }] }, 200)),
    );

    await expect(listSessionTodos("ses_analysis_12345678")).rejects.toThrow(
      "Todo list entry is invalid",
    );
  });
});

function jsonResponse(value: object, status: number): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function chatRequestBody(body: BodyInit | null | undefined): {
  history: ChatHistoryMessage[];
} {
  if (typeof body !== "string") throw new Error("Chat request body is absent");
  const parsed: unknown = JSON.parse(body);
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    !("history" in parsed) ||
    !Array.isArray(parsed.history) ||
    !parsed.history.every(isChatHistoryMessage)
  ) {
    throw new Error("Chat request body is malformed");
  }
  return { history: parsed.history };
}

function isChatHistoryMessage(value: unknown): value is ChatHistoryMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    "role" in value &&
    (value.role === "user" || value.role === "assistant") &&
    "text" in value &&
    typeof value.text === "string"
  );
}

function requestUrl(value: RequestInfo | URL): string {
  return typeof value === "string"
    ? value
    : value instanceof URL
      ? value.href
      : value.url;
}

describe("provenance", () => {
  it("returns the queries an analysis read alongside what it published", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            sourceQueries: [
              {
                artifactId: "artifact-0123456789abcdef",
                version: 1,
                sha256: "b".repeat(64),
                displayName: "query-result-1.json",
                query: "MATCH (p:patients) RETURN p.PatientId AS id",
                querySha256: "c".repeat(64),
                rowCount: 44,
                sourceAlias: "lamna",
                messageId: "msg_asked",
                executedAt: "2026-07-31T00:00:00Z",
              },
            ],
            artifacts: [],
          },
          200,
        ),
      )
      .mockResolvedValueOnce(jsonResponse({ artifacts: [] }, 200));
    vi.stubGlobal("fetch", fetchMock);

    const provenance = await readTaskProvenance("task_analysis_12345678");

    expect(provenance.sourceQueries[0]?.rowCount).toBe(44);
    expect(provenance.sourceQueries[0]?.sourceAlias).toBe("lamna");
    // Provenance is only traceable if it names the turn that asked.
    expect(provenance.sourceQueries[0]?.messageId).toBe("msg_asked");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/tasks/task_analysis_12345678/provenance",
    );
  });

  it.each([
    ["sha256", { sha256: "not-a-digest" }],
    ["querySha256", { querySha256: "not-a-digest" }],
    ["query", { query: "" }],
  ])(
    "refuses a provenance entry whose %s is invalid",
    async (_label, patch) => {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>().mockResolvedValue(
          jsonResponse(
            {
              sourceQueries: [
                {
                  artifactId: "artifact-0123456789abcdef",
                  version: 1,
                  sha256: "b".repeat(64),
                  displayName: "query-result-1.json",
                  query: "MATCH (n) RETURN n",
                  querySha256: "c".repeat(64),
                  rowCount: 1,
                  sourceAlias: null,
                  executedAt: "2026-07-31T00:00:00Z",
                  ...patch,
                },
              ],
              artifacts: [],
            },
            200,
          ),
        ),
      );

      await expect(
        readTaskProvenance("task_analysis_12345678"),
      ).rejects.toThrow("Provenance entry is invalid");
    },
  );
});

describe("query rows belong to their query", () => {
  const rows = {
    artifactId: "artifact-1111111111111111111111111111111111111111",
    version: 1,
    kind: "data",
    sha256: "a".repeat(64),
    displayName: "query-result-1.json",
    sizeBytes: 956,
  };
  const workbook = {
    artifactId: "artifact-2222222222222222222222222222222222222222",
    version: 1,
    kind: "xlsx",
    sha256: "b".repeat(64),
    displayName: "rooms.xlsx",
    sizeBytes: 20_000,
  };
  const query = {
    artifactId: rows.artifactId,
    version: 1,
    sha256: rows.sha256,
    displayName: rows.displayName,
    query: "MATCH (r:rooms) RETURN r",
    querySha256: "c".repeat(64),
    rowCount: 52,
    sourceAlias: "lamna",
    messageId: null,
    executedAt: "2026-08-06T00:41:54.000Z",
  };

  it("keeps the workbook and drops the rows from outputs", () => {
    expect(deliverableArtifacts([rows, workbook], [query])).toEqual([workbook]);
  });

  it("excludes input and execution evidence even without query provenance", () => {
    const evidence = ["input", "manifest", "script", "data"].map((kind) => ({
      ...rows,
      kind,
      displayName: "dashboard.html",
    }));
    expect(deliverableArtifacts([...evidence, workbook], [])).toEqual([
      workbook,
    ]);
  });

  it.each(["html", "xlsx", "xlsm", "pptx", "docx", "pdf", "svg", "png", "mmd"])(
    "includes published %s deliverables",
    (kind) => {
      const artifact = { ...workbook, kind };
      expect(deliverableArtifacts([artifact], [])).toEqual([artifact]);
    },
  );

  it("does not promote unknown artifact kinds to outputs", () => {
    expect(
      deliverableArtifacts([{ ...workbook, kind: "unknown" }], []),
    ).toEqual([]);
  });

  it("finds the rows a query produced", () => {
    expect(queryRowsArtifact(query, [rows, workbook])).toEqual(rows);
  });

  it("returns nothing when the rows were never published", () => {
    expect(queryRowsArtifact(query, [workbook])).toBeUndefined();
  });
});
