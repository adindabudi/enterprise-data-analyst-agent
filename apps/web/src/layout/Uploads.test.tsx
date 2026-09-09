import { withSessionHistory } from "../test/session-history";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopWorkspace } from "./DesktopWorkspace";
import { MobileWorkspace } from "./MobileWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = "eda_csrf=; Max-Age=0; Path=/";
  window.history.replaceState(null, "", "/");
});

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

describe.each([
  ["desktop", DesktopWorkspace],
  ["mobile", MobileWorkspace],
] as const)("%s secure inputs", (layout, Workspace) => {
  it("bounds an upload-first session title without shortening source metadata", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const description =
      "Regional warehouse distribution and inventory operations "
        .repeat(3)
        .trim();
    let createdTitle = "";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions") {
        const request = JSON.parse(
          typeof init?.body === "string" ? init.body : "{}",
        ) as { title: string };
        createdTitle = request.title;
        return Promise.resolve(
          jsonResponse(
            { sessionId: "ses_upload_12345678" },
            createdTitle.length > 120 ? 422 : 201,
          ),
        );
      }
      if (path.endsWith("/uploads"))
        return Promise.resolve(
          jsonResponse(
            {
              uploadId: "upl_upload_12345678",
              displayName: "inventory.csv",
              state: "clean",
            },
            202,
          ),
        );
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(
      <DesktopWorkspace
        compact={layout === "mobile"}
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
          source: { alias: "operations", description },
        }}
      />,
    );

    await user.upload(
      screen.getByLabelText("Choose analysis input"),
      new File(["item,quantity\na,2\n"], "inventory.csv"),
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          requestUrl(input).endsWith("/uploads"),
        ),
      ).toBe(true);
    });
    expect(createdTitle.length).toBeGreaterThan(0);
    expect(createdTitle.length).toBeLessThanOrEqual(120);
    expect(
      screen.getByRole("heading", { name: description, level: 2 }),
    ).toBeVisible();
    expect(
      screen.queryByText(/Upload could not be confirmed/),
    ).not.toBeInTheDocument();
  });

  it("clears a pending upload on new chat and ignores its late result", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const upload = deferredResponse();
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_upload_12345678" }, 201),
        );
      if (path.endsWith("/uploads")) return upload.promise;
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<Workspace />);
    await user.upload(
      screen.getByLabelText("Choose analysis input"),
      new File(["data"], "private.csv"),
    );
    expect(screen.getAllByText("Uploading").length).toBeGreaterThan(0);
    const newChat = screen.getAllByRole("button", { name: "New chat" })[0];
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);
    await act(async () => {
      upload.resolve(
        jsonResponse(
          {
            uploadId: "upl_upload_12345678",
            displayName: "private.csv",
            state: "clean",
          },
          202,
        ),
      );
      await upload.promise;
    });
    expect(screen.queryByText("private.csv")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Remove attachment" }),
    ).not.toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "New question",
    );
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("removes a scanning attachment without blocking an ordinary chat", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_upload_12345678" }, 201),
        );
      if (path.endsWith("/uploads"))
        return Promise.resolve(
          jsonResponse(
            {
              uploadId: "upl_upload_12345678",
              displayName: "private.csv",
              state: "scanning",
            },
            202,
          ),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_upload_12345678" }, 201),
        );
      if (path.endsWith("/chat"))
        return Promise.resolve(
          new Response(
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
            { headers: { "Content-Type": "text/event-stream" } },
          ),
        );
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<Workspace />);
    await user.upload(
      screen.getByLabelText("Choose analysis input"),
      new File(["data"], "private.csv"),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Question without file",
    );
    await user.keyboard("{Enter}");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestUrl(input).endsWith("/messages"),
      ),
    ).toBe(false);
    await user.click(screen.getByRole("button", { name: "Remove attachment" }));
    expect(screen.queryByText("private.csv")).not.toBeInTheDocument();
    expect(screen.queryByText("Deep analysis enabled")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          requestUrl(input).endsWith("/chat"),
        ),
      ).toBe(true);
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestUrl(input).endsWith("/tasks"),
      ),
    ).toBe(false);
  });

  it("uploads on attach and waits for a clean scan and explicit Send", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const session = deferredResponse();
    const upload = deferredResponse();
    const scan = deferredResponse();
    const sessionId = "ses_upload_12345678";
    const uploadId = "upl_upload_12345678";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions") return session.promise;
      if (path === `/api/sessions/${sessionId}/uploads`) return upload.promise;
      if (path === `/api/sessions/${sessionId}/uploads/${uploadId}`)
        return scan.promise;
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_upload_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse(
            {
              sessionId,
              taskId: "task_upload_12345678",
              status: "planning",
            },
            202,
          ),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<Workspace />);
    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Analyze this file",
    );
    const file = new File(["region,revenue\nwest,42"], "revenue.csv", {
      type: "text/csv",
    });
    await user.upload(screen.getByLabelText("Choose analysis input"), file);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(screen.getAllByText("Pending upload").length).toBeGreaterThan(0);
    await act(async () => {
      session.resolve(jsonResponse({ sessionId }, 201));
      await session.promise;
    });
    expect(screen.getAllByText("Uploading").length).toBeGreaterThan(0);
    const uploadCall = fetchMock.mock.calls.find(([input]) =>
      requestUrl(input).endsWith("/uploads"),
    );
    expect(uploadCall?.[1]?.body).toBeInstanceOf(FormData);
    expect((uploadCall?.[1]?.body as FormData).get("upload")).toBe(file);

    await act(async () => {
      upload.resolve(
        jsonResponse(
          { uploadId, displayName: "revenue.csv", state: "scanning" },
          202,
        ),
      );
      await upload.promise;
    });
    expect(screen.getAllByText("Scanning").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestUrl(input).endsWith(`/${uploadId}`),
      ),
    ).toBe(false);
    await user.click(
      screen.getByRole("button", { name: "Refresh upload status" }),
    );
    expect(
      screen.getByRole("button", { name: "Refresh upload status" }),
    ).toBeDisabled();
    await act(async () => {
      scan.resolve(
        jsonResponse({ uploadId, displayName: "revenue.csv", state: "clean" }),
      );
      await scan.promise;
    });
    expect(screen.getAllByText("Clean").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestUrl(input).endsWith("/tasks"),
      ),
    ).toBe(false);

    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      const taskCall = fetchMock.mock.calls.find(([input]) =>
        requestUrl(input).endsWith("/tasks"),
      );
      expect(taskCall?.[0]).toBe(`/api/sessions/${sessionId}/tasks`);
      const body = taskCall?.[1]?.body;
      if (typeof body !== "string")
        throw new Error("Expected a JSON task request");
      expect(JSON.parse(body)).toMatchObject({ inputUploadIds: [uploadId] });
    });
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => requestUrl(input) === "/api/sessions",
      ),
    ).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        requestUrl(input).endsWith("/uploads"),
      ),
    ).toHaveLength(1);
  });
});
