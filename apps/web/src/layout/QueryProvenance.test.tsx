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
import { QueryProvenance } from "./QueryProvenance";
import { readDataStep } from "../chat/data-step";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = "eda_csrf=; Max-Age=0; Path=/";
  window.history.replaceState(null, "", "/");
});

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

const firstQuery = {
  artifactId: "artifact-first_12345678",
  version: 1,
  sha256: "b".repeat(64),
  displayName: "first-query.json",
  query: "MATCH (room:rooms) RETURN count(room)",
  querySha256: "a".repeat(64),
  rowCount: 1,
  sourceAlias: "lamna-healthcare",
  messageId: "msg_first_12345678",
  executedAt: "2026-09-06T01:00:00Z",
};
const secondQuery = {
  ...firstQuery,
  artifactId: "artifact-second_12345678",
  messageId: "msg_second_12345678",
  query: "MATCH (patient:patients) RETURN count(patient)",
  querySha256: "c".repeat(64),
  sha256: "d".repeat(64),
};
const firstStep = {
  stepId: "first-query",
  kind: "gql",
  label: "Read rooms",
  state: "completed",
  query: firstQuery.query,
  querySha256: firstQuery.querySha256,
  resultSha256: firstQuery.sha256,
  source: "lamna-healthcare",
  rowCount: "1",
};

describe("canonical question provenance", () => {
  it("does not claim an old answer had no queries when records are unavailable", () => {
    render(
      <QueryProvenance
        messages={[
          {
            id: "msg_question_12345678",
            role: "user",
            text: "A saved question",
          },
          {
            id: "msg_answer_12345678",
            role: "assistant",
            text: "A saved answer",
          },
        ]}
        liveSteps={[]}
        queries={[]}
        artifacts={[]}
        taskId={null}
      />,
    );
    expect(
      screen.getByText(
        "No query records are available for this saved conversation.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("No queries yet.")).not.toBeInTheDocument();
  });

  it("restores query provenance for inline answers without a background task", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?session=ses_provenance_12345678");
    const energyStep = {
      ...firstStep,
      query: "MATCH (w:Well) RETURN count(*) AS wells",
      source: "indonesia-upstream",
      executedAt: "2026-09-15T01:00:01Z",
    };
    const fallback = withSessionHistory(
      vi.fn<typeof fetch>((input) =>
        Promise.reject(new Error(`Unexpected request: ${requestUrl(input)}`)),
      ),
    );
    const fetcher = vi.fn<typeof fetch>((input, options) =>
      requestUrl(input).endsWith("/history")
        ? Promise.resolve(
            jsonResponse({
              messages: [
                {
                  messageId: "msg_question_12345678",
                  role: "user",
                  text: "Berapa jumlah sumur?",
                  createdAt: "2026-09-15T01:00:00Z",
                  taskId: null,
                  steps: [energyStep],
                },
                {
                  messageId: "msg_answer_12345678",
                  role: "assistant",
                  text: "Ada 12 sumur.",
                  createdAt: "2026-09-15T01:00:02Z",
                  taskId: null,
                },
              ],
              tasks: [],
            }),
          )
        : fallback(input, options),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<DesktopWorkspace />);
    expect(await screen.findByText("Ada 12 sumur.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    const group = await screen.findByRole("group", {
      name: "Berapa jumlah sumur?",
    });
    expect(within(group).getByText(energyStep.query)).toBeVisible();
    expect(
      within(group).getByText("Read 1 row from indonesia-upstream"),
    ).toBeVisible();
    expect(screen.queryByText("No queries yet.")).not.toBeInTheDocument();
    expect(
      fetcher.mock.calls.some(([input]) =>
        requestUrl(input).endsWith("/provenance"),
      ),
    ).toBe(false);
  });

  it("does not duplicate one query when saved and streamed records overlap", () => {
    const step = readDataStep(firstStep);
    if (!step) throw new Error("Test query step is invalid");
    render(
      <QueryProvenance
        messages={[
          {
            id: "msg_user_12345678",
            role: "user",
            text: "How many rooms?",
            steps: [step],
          },
          {
            id: "msg_reply_12345678",
            role: "assistant",
            text: "A room count.",
            steps: [step],
          },
        ]}
        liveSteps={[{ ...step, state: "running" }]}
        queries={[]}
        artifacts={[]}
        taskId={null}
      />,
    );
    const group = screen.getByRole("group", { name: "How many rooms?" });
    expect(within(group).getAllByText(firstQuery.query)).toHaveLength(1);
    expect(within(group).queryByText("Running query")).not.toBeInTheDocument();
    expect(
      within(group).getByText("Read 1 row from lamna-healthcare"),
    ).toBeVisible();
  });

  it("groups task provenance by the original question after runs complete and clears it on new chat", async () => {
    class EventSourceFixture {
      static current: EventSourceFixture | undefined;
      readonly listeners = new Map<string, EventListener>();

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
    const user = userEvent.setup();
    document.cookie = "eda_csrf=csrf-value; Path=/";
    let messageCount = 0;
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>((input) => {
          const path = requestUrl(input);
          if (path === "/api/sessions")
            return Promise.resolve(
              jsonResponse({ sessionId: "ses_questions_12345678" }, 201),
            );
          if (path.endsWith("/messages")) {
            messageCount += 1;
            const messageId =
              messageCount === 1 ? firstQuery.messageId : secondQuery.messageId;
            return Promise.resolve(jsonResponse({ messageId }, 201));
          }
          if (path.endsWith("/tasks"))
            return Promise.resolve(
              jsonResponse(
                {
                  taskId:
                    messageCount === 1
                      ? "task_questions_12345678"
                      : "task_questions_87654321",
                },
                202,
              ),
            );
          if (path.endsWith("/provenance"))
            return Promise.resolve(
              jsonResponse({
                sourceQueries: path.includes("task_questions_12345678")
                  ? [firstQuery]
                  : [secondQuery],
                artifacts: [],
              }),
            );
          if (path.endsWith("/artifacts"))
            return Promise.resolve(jsonResponse({ artifacts: [] }));
          if (path.includes("/messages/msg_reply_"))
            return Promise.resolve(
              jsonResponse({
                messageId: path.includes("first")
                  ? "msg_reply_first_12345678"
                  : "msg_reply_second_12345678",
                role: "assistant",
                text: path.includes("first")
                  ? "First answer."
                  : "Second answer.",
              }),
            );
          return Promise.reject(new Error(`Unexpected request: ${path}`));
        }),
      ),
    );
    render(<DesktopWorkspace />);
    const composer = screen.getByRole("textbox", { name: "Analysis request" });

    await user.type(composer, "How many rooms?");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(EventSourceFixture.current).toBeDefined();
    });
    act(() => {
      EventSourceFixture.current?.emit("run.completed", {
        eventId: "evt_first_12345678",
        sequence: 1,
        sessionId: "ses_questions_12345678",
        taskId: "task_questions_12345678",
        occurredAt: "2026-09-06T01:00:00Z",
        type: "run.completed",
        payload: {
          status: "completed",
          finalMessageId: "msg_reply_first_12345678",
        },
        provenanceRefs: [],
      });
    });
    expect(await screen.findByText("Analysis completed")).toBeVisible();

    await user.type(composer, "How many patients?");
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(window.location.search).toContain("task_questions_87654321");
    });
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    const secondGroup = await screen.findByRole("group", {
      name: "How many patients?",
    });
    const firstGroup = screen.getByRole("group", { name: "How many rooms?" });
    expect(within(firstGroup).getByText(firstQuery.query)).toBeVisible();
    expect(
      within(firstGroup).queryByText(secondQuery.query),
    ).not.toBeInTheDocument();
    expect(within(secondGroup).getByText(secondQuery.query)).toBeVisible();

    const newChat = screen.getAllByRole("button", { name: "New chat" })[0];
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    expect(screen.getByText("No queries yet.")).toBeVisible();
    expect(screen.queryByText(firstQuery.query)).not.toBeInTheDocument();
    expect(screen.queryByText(secondQuery.query)).not.toBeInTheDocument();
  });

  it("labels missing question text on resume without inventing it", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/?task=task_questions_12345678");
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>((input) =>
          Promise.resolve(
            requestUrl(input).endsWith("/provenance")
              ? jsonResponse({ sourceQueries: [firstQuery], artifacts: [] })
              : jsonResponse({ artifacts: [] }),
          ),
        ),
      ),
    );
    render(<DesktopWorkspace />);
    await waitFor(() => {
      expect(window.location.search).toContain("session=");
    });
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    const group = await screen.findByRole("group", {
      name: "Question text unavailable",
    });
    expect(within(group).getByText(firstQuery.query)).toBeVisible();
    expect(
      screen.queryByRole("group", { name: "How many rooms?" }),
    ).not.toBeInTheDocument();
  });

  it("retains failed attempts under their question after a later turn", () => {
    const failedStep = readDataStep({
      ...firstStep,
      state: "failed",
      detail: "Query rejected",
    });
    if (!failedStep) throw new Error("Test query step is invalid");

    render(
      <QueryProvenance
        messages={[
          {
            id: firstQuery.messageId,
            role: "user",
            text: "How many rooms?",
            steps: [failedStep],
          },
          {
            id: secondQuery.messageId,
            role: "user",
            text: "Another question",
          },
          {
            id: "msg_reply_second_12345678",
            role: "assistant",
            text: "Second answer.",
          },
        ]}
        liveSteps={[]}
        queries={[]}
        artifacts={[]}
        taskId={null}
      />,
    );

    const group = screen.getByRole("group", { name: "How many rooms?" });
    expect(within(group).getByText("Failed query")).toBeVisible();
    expect(within(group).getByText(firstQuery.query)).toBeVisible();
  });
});
