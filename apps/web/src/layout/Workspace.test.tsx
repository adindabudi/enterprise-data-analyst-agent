import { withSessionHistory } from "../test/session-history";
import {
  act,
  cleanup,
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

    expect(
      screen.getByText(
        "Answers chat questions; deep analysis awaits acceptance",
      ),
    ).toBeVisible();

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

    await user.click(trigger);
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
    await user.click(screen.getByRole("button", { name: "Add context" }));
    await user.click(screen.getByRole("menuitem", { name: "Deep analysis" }));
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
    expect(screen.queryByText("Deep analysis enabled")).not.toBeInTheDocument();
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

    await user.click(screen.getByRole("button", { name: "Add context" }));
    await user.click(screen.getByRole("menuitem", { name: "Deep analysis" }));
    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "Compare FY2026 regional revenue",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      within(
        screen.getByRole("region", { name: "Analysis conversation" }),
      ).getByText("Compare FY2026 regional revenue"),
    ).toBeVisible();
    expect(await screen.findAllByText("Agent is thinking")).toHaveLength(2);
    expect(window.location.search).toBe(
      "?session=ses_analysis_12345678&task=task_analysis_12345678",
    );
    expect(
      screen.queryByText(/control total is \$1,250,000\.50/i),
    ).not.toBeInTheDocument();
  });

  it("uses direct streaming for Ask without creating a durable task", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_chat_12345678",
            title: "Lamna healthcare operations",
          }),
          {
            status: 201,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_chat_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          [
            'event: status\ndata: {"message":"Agent is thinking"}\n\n',
            'event: delta\ndata: {"text":"Hello from the direct agent."}\n\n',
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          ].join(""),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "Explain decimal precision",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      await screen.findByText("Hello from the direct agent."),
    ).toBeVisible();
    expect(
      fetchMock.mock.calls.some(([path]) =>
        requestUrl(path).includes("/tasks"),
      ),
    ).toBe(false);
    expect(window.location.search).toBe("?session=ses_chat_12345678");
  });

  it("keeps the reads of a finished turn with the answer they produced", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const step = {
      stepId: "step-1",
      kind: "gql",
      label: "Queried lamna-healthcare graph",
      state: "completed",
      query: "MATCH (r:rooms) RETURN count(*) AS total",
      source: "lamna-healthcare",
      rowCount: "24",
      querySha256: "a".repeat(64),
    };
    const failedStep = {
      ...step,
      stepId: "step-2",
      state: "failed",
      query: "MATCH (a)-[]->(b) RETURN a",
      querySha256: "b".repeat(64),
      rowCount: undefined,
      detail: "The relationship pattern does not match any edge type.",
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_chat_12345678",
            title: "Lamna healthcare operations",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_chat_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          [
            `event: data_step\ndata: ${JSON.stringify({ ...step, state: "running" })}\n\n`,
            `event: data_step\ndata: ${JSON.stringify(step)}\n\n`,
            `event: data_step\ndata: ${JSON.stringify(failedStep)}\n\n`,
            'event: delta\ndata: {"text":"24 kamar."}\n\n',
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          ].join(""),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "berapa kamar?",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("24 kamar.")).toBeVisible();
    // One read reported twice is one step; the separate failed attempt is another.
    const summary = await screen.findByRole("button", {
      name: /Analyzed · 2 data steps/,
    });
    expect(summary).toBeVisible();
    const [firstQueryButton] = screen.getAllByRole("button", {
      name: "Queried lamna-healthcare graph",
    });
    if (!firstQueryButton)
      throw new Error("Expected the first graph query step");
    await user.click(firstQueryButton);
    expect(screen.getByText(step.query)).toBeVisible();
    expect(screen.getByText(/24 rows/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Provenance" }));
    const provenance = screen.getByRole("region", { name: "Provenance" });
    const question = within(provenance).getByRole("group", {
      name: "berapa kamar?",
    });
    expect(
      within(question).getByText("Read 24 rows from lamna-healthcare"),
    ).toBeVisible();
    expect(within(provenance).getByText(step.query)).toBeVisible();
    expect(within(provenance).getByText(/query SHA-256 a{12}…/)).toBeVisible();
    expect(within(question).getByText(failedStep.query)).toBeVisible();
    expect(within(question).getByText("Failed query")).toBeVisible();
  });

  it("keeps follow-up messages in one backend session", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_thread_12345678",
            title: "Lamna healthcare operations",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_thread_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: delta\ndata: {"text":"First answer."}\n\n' +
            'event: completed\ndata: {"messageId":"msg_reply_12345678"}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_followup_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: delta\ndata: {"text":"Second answer."}\n\n' +
            'event: completed\ndata: {"messageId":"msg_reply_87654321"}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);
    const composer = screen.getByRole("textbox", {
      name: "Analysis request",
    });

    await user.type(composer, "First question");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("First answer.")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "First question" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "First question" }),
    ).toHaveAttribute("aria-current", "page");
    await user.type(composer, "Follow up");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Second answer.")).toBeVisible();

    expect(
      fetchMock.mock.calls.filter(
        ([path]) => requestUrl(path) === "/api/sessions",
      ),
    ).toHaveLength(1);
    expect(fetchMock.mock.calls.map(([path]) => requestUrl(path))).toContain(
      "/api/sessions/ses_thread_12345678/messages",
    );
  });

  it("stops reporting progress for reads the reader interrupted", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const step = {
      stepId: "step-1",
      kind: "gql",
      label: "Queried lamna-healthcare graph",
      state: "running",
      query: "MATCH (r:rooms) RETURN count(*) AS total",
      source: "lamna-healthcare",
      querySha256: "a".repeat(64),
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_stop_12345678",
            title: "Lamna healthcare operations",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_stop_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) =>
        Promise.resolve(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: data_step\ndata: ${JSON.stringify(step)}\n\n`,
                  ),
                );
                // The turn stays open until Stop, exactly as an unfinished fetch would.
                init?.signal?.addEventListener("abort", () => {
                  controller.error(new DOMException("Aborted", "AbortError"));
                });
              },
            }),
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          ),
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "berapa kamar?",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText(step.label)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Stop current task" }));

    // A spinner that never stops claims a read is still running after the turn ended.
    expect(await screen.findByText("Response stopped")).toBeVisible();
    expect(
      screen.queryByRole("progressbar", { name: "Reading the source" }),
    ).not.toBeInTheDocument();
  });

  it("ignores an aborted turn after switching to a new chat", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_stale_12345678",
            title: "Lamna healthcare operations",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_stale_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockImplementationOnce(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Slow question",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeEnabled();
    const newChat = screen.getAllByRole("button", { name: "New chat" }).at(0);
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);

    expect(await screen.findByText("Ready")).toBeVisible();
    expect(screen.queryByText("Response stopped")).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Analysis request" }),
    ).toBeEnabled();
  });

  it("follows a model-selected deep-analysis handoff without a second client task request", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const step = {
      stepId: "step-handoff",
      kind: "gql",
      label: "Read room occupancy",
      state: "completed",
      query: "MATCH (room:rooms) RETURN room.RoomType, count(room)",
      source: "lamna-healthcare",
      querySha256: "a".repeat(64),
      rowCount: "5",
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_auto_12345678",
            title: "Lamna healthcare operations",
          }),
          {
            status: 201,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ messageId: "msg_auto_12345678" }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          [
            'event: status\ndata: {"message":"Agent is thinking"}\n\n',
            `event: data_step\ndata: ${JSON.stringify(step)}\n\n`,
            'event: analysis_started\ndata: {"taskId":"task_auto_12345678"}\n\n',
          ].join(""),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<DesktopWorkspace />);

    await user.type(
      screen.getByPlaceholderText("Ask about your analysis"),
      "Build and validate a workbook",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      await screen.findAllByText("Running a deeper analysis"),
    ).not.toHaveLength(0);
    const dataSteps = screen.getByRole("region", { name: "Data steps" });
    expect(dataSteps.closest("article")).toHaveTextContent(
      "Build and validate a workbook",
    );
    expect(
      screen.queryByRole("progressbar", { name: "Reading the source" }),
    ).not.toBeInTheDocument();
    await user.click(
      within(dataSteps).getByRole("button", { name: /Analyzed · 1 data step/ }),
    );
    await user.click(
      within(dataSteps).getByRole("button", { name: step.label }),
    );
    expect(within(dataSteps).getByText(step.query)).toBeVisible();
    expect(window.location.search).toBe(
      "?session=ses_auto_12345678&task=task_auto_12345678",
    );
    expect(
      fetchMock.mock.calls.some(
        ([path, init]) =>
          requestUrl(path).includes("/tasks") && init?.method === "POST",
      ),
    ).toBe(false);
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

  it("uses direct Ask streaming on compact layouts", async () => {
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessionId: "ses_mobile_chat_12345678",
            title: "Private chat",
          }),
          {
            status: 201,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ messageId: "msg_mobile_chat_12345678" }),
          {
            status: 201,
            headers: { "Content-Type": "application/json" },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          'event: delta\ndata: {"text":"Compact direct response."}\n\n' +
            'event: completed\ndata: {"messageId":"msg_mobile_reply_12345678"}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    render(<MobileWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Explain this briefly",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Compact direct response.")).toBeVisible();
    expect(
      fetchMock.mock.calls.some(([path]) =>
        requestUrl(path).includes("/tasks"),
      ),
    ).toBe(false);
    expect(window.location.search).toBe("?session=ses_mobile_chat_12345678");
  });
});

function requestUrl(value: RequestInfo | URL): string {
  return typeof value === "string"
    ? value
    : value instanceof URL
      ? value.href
      : value.url;
}
