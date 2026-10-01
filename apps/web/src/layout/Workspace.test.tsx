import { withSessionHistory } from "../test/session-history";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

describe("workspace layout", () => {
  it("renders three stable desktop regions", () => {
    render(
      <DesktopWorkspace
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "unlinked",
          chatQuery: false,
          source: {
            alias: "logistics",
            description: "Logistics shipment ontology",
          },
        }}
      />,
    );

    expect(screen.getByRole("navigation", { name: "Analyses" })).toBeVisible();
    expect(screen.getByRole("main")).toBeVisible();
    expect(
      screen.getByRole("complementary", { name: "Analysis details" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Send message" })).toBeVisible();
    expect(
      screen.getByRole("heading", {
        name: "New analysis",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", {
        name: "Logistics shipment ontology",
        level: 2,
      }),
    ).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Inputs" })).getByText(
        "Logistics shipment ontology",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText(/Lamna|hospitals|healthcare/),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Sign in required")).toBeVisible();
    expect(screen.queryByText("fy2026-revenue.xlsx")).not.toBeInTheDocument();
  });

  it("renders one primary mobile region", () => {
    render(<MobileWorkspace />);

    expect(screen.getByRole("main")).toBeVisible();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "New analysis",
      }),
    ).toBeVisible();
  });

  it("follows changed ontology metadata while the conversation is still new", () => {
    const { rerender } = render(
      <DesktopWorkspace
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
          source: {
            alias: "logistics",
            description: "Logistics shipment ontology",
          },
        }}
      />,
    );
    expect(
      screen.getByRole("heading", {
        name: "Logistics shipment ontology",
        level: 2,
      }),
    ).toBeVisible();

    rerender(
      <DesktopWorkspace
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
          source: {
            alias: "energy",
            description: "Energy consumption ontology",
          },
        }}
      />,
    );

    expect(
      screen.getByRole("heading", {
        name: "Energy consumption ontology",
        level: 2,
      }),
    ).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Inputs" })).getByText(
        "Energy consumption ontology",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Logistics shipment ontology"),
    ).not.toBeInTheDocument();
  });

  it("ignores a task parameter that is not a task id", () => {
    window.history.replaceState(null, "", "/?task=not-a-task");

    render(<DesktopWorkspace />);

    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeDisabled();
  });

  it("shows ontology readiness separately from delegated connection", () => {
    const { rerender } = render(
      <DesktopWorkspace
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: false,
        }}
      />,
    );

    expect(screen.getByText("Signed in; not ready for queries")).toBeVisible();

    rerender(
      <DesktopWorkspace
        fabricAvailability="configured"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
        }}
      />,
    );

    expect(screen.getByText("Connected; acceptance pending")).toBeVisible();

    rerender(
      <DesktopWorkspace
        fabricAvailability="ready"
        fabricAuthorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
        }}
      />,
    );

    expect(screen.getByText("Connected and ready")).toBeVisible();
  });

  it("opens and restores focus from the mobile analyses drawer", async () => {
    const user = userEvent.setup();
    render(<MobileWorkspace />);
    const trigger = screen.getByRole("button", { name: "Analyses" });

    fireEvent.click(trigger);
    expect(
      await screen.findByRole("dialog", { name: "Analyses drawer" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Close analyses" }));

    expect(trigger).toHaveFocus();
  });

  it("starts a clean focused desktop chat without exposing a fake refresh action", async () => {
    const user = userEvent.setup();
    render(<DesktopWorkspace />);
    const composer = screen.getByRole("textbox", {
      name: "Analysis request",
    });

    await user.type(composer, "Unsent draft");
    const newChat = screen.getAllByRole("button", { name: "New chat" }).at(0);
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);

    const newComposer = screen.getByRole("textbox", {
      name: "Analysis request",
    });
    expect(
      screen.queryByRole("button", { name: "Refresh analysis" }),
    ).not.toBeInTheDocument();
    expect(newComposer).toHaveValue("");
    expect(newComposer).toHaveFocus();
    expect(
      screen.getByRole("heading", { name: "New private analysis" }),
    ).toBeVisible();
  });

  it("offers an immediate focused new-chat action on mobile", async () => {
    const user = userEvent.setup();
    render(<MobileWorkspace />);
    const composer = screen.getByRole("textbox", {
      name: "Analysis request",
    });

    await user.type(composer, "Mobile draft");
    await user.click(screen.getByRole("button", { name: "New chat" }));

    const newComposer = screen.getByRole("textbox", {
      name: "Analysis request",
    });
    expect(newComposer).toHaveValue("");
    expect(newComposer).toHaveFocus();
    expect(
      screen.getByRole("heading", { name: "New private analysis" }),
    ).toBeVisible();
  });

  it("starts a real durable analysis without fabricating completion", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi
          .fn<typeof fetch>()
          .mockResolvedValueOnce(
            new Response(
              JSON.stringify({
                sessionId: "ses_analysis_12345678",
                title: "Lamna healthcare operations",
              }),
              {
                status: 201,
                headers: { "Content-Type": "application/json" },
              },
            ),
          )
          .mockResolvedValueOnce(
            new Response(
              JSON.stringify({ messageId: "msg_analysis_12345678" }),
              {
                status: 201,
                headers: { "Content-Type": "application/json" },
              },
            ),
          )
          .mockResolvedValueOnce(
            new Response(
              JSON.stringify({
                taskId: "task_analysis_12345678",
                sessionId: "ses_analysis_12345678",
                status: "planning",
                checkpointSequence: 0,
                activeAttemptId: null,
                finalMessageId: null,
              }),
              { status: 202, headers: { "Content-Type": "application/json" } },
            ),
          ),
      ),
    );
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "Compare FY2026 regional revenue",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      within(
        screen.getByRole("region", { name: "Analysis conversation" }),
      ).getByText("Compare FY2026 regional revenue"),
    ).toBeVisible();
    expect(await screen.findAllByText("Working")).not.toHaveLength(0);
    expect(window.location.search).toBe(
      "?session=ses_analysis_12345678&task=task_analysis_12345678",
    );
    expect(
      screen.queryByText(/control total is \$1,250,000\.50/i),
    ).not.toBeInTheDocument();
  });

  it("sends default requests through the durable task endpoint", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse(
            { sessionId: "ses_chat_12345678", title: "Private chat" },
            201,
          ),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_chat_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_chat_12345678" }, 202),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "Explain decimal precision",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(window.location.search).toBe(
        "?session=ses_chat_12345678&task=task_chat_12345678",
      );
    });
    expect(
      fetchMock.mock.calls.some(([path]) => requestUrl(path).includes("/chat")),
    ).toBe(false);
    const taskCall = fetchMock.mock.calls.find(([path]) =>
      requestUrl(path).endsWith("/tasks"),
    );
    expect(taskCall?.[1]?.body).toBe(
      JSON.stringify({ messageId: "msg_chat_12345678", history: [] }),
    );
  });

  it("shows a retry-later status without dropping the question when the queue is full", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_queue_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_queue_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(jsonResponse({ title: "Queue full" }, 429));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Try a queued analysis",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      (
        await screen.findAllByText(
          "The analysis queue is full. Try again shortly.",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(
      within(
        screen.getByRole("region", { name: "Analysis conversation" }),
      ).getByText("Try a queued analysis"),
    ).toBeVisible();
  });

  it("keeps task provenance with the question that produced it", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const query = {
      artifactId: "artifact-rooms_12345678",
      version: 1,
      sha256: "b".repeat(64),
      displayName: "rooms.json",
      query: "MATCH (r:rooms) RETURN count(*) AS total",
      querySha256: "a".repeat(64),
      rowCount: 24,
      sourceAlias: "lamna-healthcare",
      messageId: "msg_chat_12345678",
      executedAt: "2026-09-06T01:00:00Z",
    };
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_chat_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_chat_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_chat_12345678" }, 202),
        );
      if (path.endsWith("/provenance"))
        return Promise.resolve(
          jsonResponse({ sourceQueries: [query], artifacts: [] }),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "berapa kamar?",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(window.location.search).toContain("task_chat_12345678");
    });
    await user.click(screen.getByRole("button", { name: "Provenance" }));

    const group = await screen.findByRole("group", { name: "berapa kamar?" });
    expect(within(group).getByText(query.query)).toBeVisible();
    expect(
      within(group).getByText("Read 24 rows from lamna-healthcare"),
    ).toBeVisible();
  });

  it("sends a second submission as steering while a task is running", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_steer_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_steer_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_steer_12345678" }, 202),
        );
      if (path.endsWith("/steer"))
        return Promise.resolve(
          jsonResponse({ commandId: "cmd_steer_12345678" }, 202),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);
    const composer = screen.getByRole("textbox", { name: "Analysis request" });

    await user.type(composer, "First question");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(window.location.search).toContain("task_steer_12345678");
    });
    await user.type(composer, "Also include occupancy");
    await user.click(
      screen.getByRole("button", { name: "Send to the running analysis" }),
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([path]) =>
          requestUrl(path).endsWith("/steer"),
        ),
      ).toBe(true);
    });
    expect(screen.getByText("Sent to the running analysis")).toBeVisible();
  });

  it("cancels the current durable task when stopped", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_stop_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_stop_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_stop_12345678" }, 202),
        );
      if (path.endsWith("/cancel"))
        return Promise.resolve(
          jsonResponse({ commandId: "cmd_cancel_12345678" }, 202),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Slow question",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Stop current task" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Stop current task" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([path]) =>
          requestUrl(path).endsWith("/cancel"),
        ),
      ).toBe(true);
    });
    expect(
      screen.getByText("Cancellation queued for the next checkpoint"),
    ).toBeVisible();
  });

  it("keeps a detached task out of a new chat", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_stale_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_stale_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_stale_12345678" }, 202),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Slow question",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(window.location.search).toContain("task_stale_12345678");
    });
    const newChat = screen.getAllByRole("button", { name: "New chat" }).at(0);
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);

    expect(await screen.findByText("Ready")).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "Analysis request" }),
    ).toBeEnabled();
    expect(window.location.search).toBe("");
  });

  it("shows the plan as the agent writes it, before the run finishes", async () => {
    class EventSourceFixture {
      static current: EventSourceFixture | undefined;
      readonly listeners = new Map<
        string,
        (event: MessageEvent<string>) => void
      >();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(readonly url: string) {
        EventSourceFixture.current = this;
      }

      addEventListener(type: string, listener: EventListener): void {
        this.listeners.set(type, listener);
      }

      close(): void {}

      emit(type: string, value: object, id: string): void {
        this.listeners.get(type)?.(
          new MessageEvent(type, {
            data: JSON.stringify(value),
            lastEventId: id,
          }),
        );
      }
    }
    Object.defineProperty(globalThis, "EventSource", {
      configurable: true,
      value: EventSourceFixture,
      writable: true,
    });
    window.history.replaceState(null, "", "/?task=task_live_plan_12345678");
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>(() =>
          Promise.resolve(new Response(JSON.stringify({ artifacts: [] }))),
        ),
      ),
    );
    render(<DesktopWorkspace />);
    await waitFor(() => {
      expect(EventSourceFixture.current).toBeDefined();
    });
    const plan = (completed: boolean) => ({
      eventId: `evt_plan_${String(completed)}_12345678`,
      sequence: completed ? 3 : 2,
      sessionId: "ses_live_plan_12345678",
      taskId: "task_live_plan_12345678",
      occurredAt: "2026-09-24T00:00:00Z",
      type: "todo.updated",
      payload: [
        {
          todoId: "1",
          text: "Susun rencana dashboard okupansi",
          description: "Tetapkan ruang lingkup dashboard.",
          completed,
        },
        { todoId: "2", text: "Bangun dashboard interaktif", completed: false },
      ],
      provenanceRefs: [],
    });

    act(() => {
      EventSourceFixture.current?.emit(
        "todo.updated",
        plan(false),
        "1700000000000-1",
      );
    });
    expect(
      await screen.findByText("Susun rencana dashboard okupansi"),
    ).toBeVisible();
    expect(screen.getByText("Tetapkan ruang lingkup dashboard.")).toBeVisible();
    expect(screen.getByText("0 / 2")).toBeVisible();

    act(() => {
      EventSourceFixture.current?.emit(
        "todo.updated",
        plan(true),
        "1700000000000-2",
      );
    });
    expect(await screen.findByText("1 / 2")).toBeVisible();
  });

  it("shows live thinking feedback on compact layouts", async () => {
    class EventSourceFixture {
      static current: EventSourceFixture | undefined;
      readonly listeners = new Map<
        string,
        (event: MessageEvent<string>) => void
      >();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(readonly url: string) {
        EventSourceFixture.current = this;
      }

      addEventListener(type: string, listener: EventListener): void {
        this.listeners.set(type, listener);
      }

      close(): void {}

      emit(type: string, value: object): void {
        this.listeners.get(type)?.(
          new MessageEvent(type, {
            data: JSON.stringify(value),
            lastEventId: "1700000000000-0",
          }),
        );
      }
    }
    Object.defineProperty(globalThis, "EventSource", {
      configurable: true,
      value: EventSourceFixture,
      writable: true,
    });
    window.history.replaceState(null, "", "/?task=task_mobile_12345678");
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>(() =>
          Promise.resolve(new Response(JSON.stringify({ artifacts: [] }))),
        ),
      ),
    );
    render(<MobileWorkspace />);
    await waitFor(() => {
      expect(EventSourceFixture.current).toBeDefined();
    });

    act(() => {
      EventSourceFixture.current?.emit("analysis_progress", {
        eventId: "evt_thinking_12345678",
        sequence: 1,
        sessionId: "ses_mobile_12345678",
        taskId: "task_mobile_12345678",
        occurredAt: "2026-07-28T00:00:00Z",
        type: "analysis_progress",
        payload: {
          milestone: "Agent is thinking",
          detail: "Preparing tools and response.",
          state: "running",
        },
        provenanceRefs: [],
      });
    });

    expect(screen.getAllByText("Agent is thinking")).toHaveLength(3);
    expect(screen.getByRole("status")).toHaveTextContent("Agent is thinking");
    await userEvent.click(
      screen.getByRole("button", { name: "Agent is thinking" }),
    );
    expect(screen.getByText("Preparing tools and response.")).toBeVisible();
  });

  it("uses durable tasks on compact layouts", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = requestUrl(input);
      if (path === "/api/sessions")
        return Promise.resolve(
          jsonResponse({ sessionId: "ses_mobile_chat_12345678" }, 201),
        );
      if (path.endsWith("/messages"))
        return Promise.resolve(
          jsonResponse({ messageId: "msg_mobile_chat_12345678" }, 201),
        );
      if (path.endsWith("/tasks") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ taskId: "task_mobile_chat_12345678" }, 202),
        );
      if (path.endsWith("/artifacts"))
        return Promise.resolve(jsonResponse({ artifacts: [] }));
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<MobileWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Explain this briefly",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(window.location.search).toBe(
        "?session=ses_mobile_chat_12345678&task=task_mobile_chat_12345678",
      );
    });
    expect(
      fetchMock.mock.calls.some(([path]) => requestUrl(path).includes("/chat")),
    ).toBe(false);
  });
});

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(value: RequestInfo | URL): string {
  return typeof value === "string"
    ? value
    : value instanceof URL
      ? value.href
      : value.url;
}
