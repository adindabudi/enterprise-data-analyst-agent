export function withSessionHistory(fetcher: typeof fetch): typeof fetch {
  return (input, options) => {
    const path = input instanceof Request ? input.url : String(input);
    const method =
      options?.method ?? (input instanceof Request ? input.method : "GET");
    const response = (body: object) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          headers: { "Content-Type": "application/json" },
        }),
      );
    const timestamp = "2026-09-08T00:00:00Z";
    const params = new URLSearchParams(window.location.search);
    const taskId = params.get("task");
    const sessionId = params.get("session") ?? "ses_fixture_12345678";
    const session = {
      sessionId,
      title: "Lamna healthcare operations",
      lastActivityAt: timestamp,
    };
    if (method === "GET") {
      if (path === "/api/sessions") return response([]);
      if (/^\/api\/tasks\/task_[\w-]+$/.test(path))
        return response({
          taskId: path.split("/").at(-1),
          sessionId,
          status: "analyzing",
          finalMessageId: null,
        });
      if (/^\/api\/sessions\/ses_[\w-]+$/.test(path))
        return response({ ...session, sessionId: path.split("/").at(-1) });
      if (path.endsWith("/history"))
        return response({
          messages: [],
          tasks: taskId
            ? [
                {
                  taskId,
                  status: "analyzing",
                  sourceMessageId: null,
                  finalMessageId: null,
                  createdAt: timestamp,
                  updatedAt: timestamp,
                },
              ]
            : [],
        });
      if (path.endsWith("/todos")) return response({ items: [] });
    }
    return fetcher(input, options);
  };
}
