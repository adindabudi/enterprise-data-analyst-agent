import { withSessionHistory } from "../test/session-history";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopWorkspace } from "./DesktopWorkspace";

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

function eventResponse(events: Array<[string, object]>): Response {
  return new Response(
    events
      .map(
        ([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
      )
      .join(""),
    { headers: { "Content-Type": "text/event-stream" } },
  );
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
  it.each(["deep analysis", "interactive handoff"])(
    "groups reads by the original question for %s and clears them on new chat",
    async (mode) => {
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
              return Promise.resolve(
                jsonResponse(
                  {
                    messageId:
                      messageCount === 1
                        ? firstQuery.messageId
                        : secondQuery.messageId,
                  },
                  201,
                ),
              );
            }
            if (path.endsWith("/chat"))
              return Promise.resolve(
                messageCount === 1
                  ? eventResponse([
                      ["data_step", firstStep],
                      ["delta", { text: "First answer." }],
                      ["completed", { messageId: "msg_reply_first_12345678" }],
                    ])
                  : eventResponse([
                      [
                        "analysis_started",
                        { taskId: "task_questions_12345678" },
                      ],
                    ]),
              );
            if (path.endsWith("/tasks"))
              return Promise.resolve(
                jsonResponse({ taskId: "task_questions_12345678" }, 202),
              );
            if (path.endsWith("/provenance"))
              return Promise.resolve(
                jsonResponse({
                  sourceQueries: [firstQuery, secondQuery],
                  artifacts: [],
                }),
              );
            if (path.endsWith("/artifacts"))
              return Promise.resolve(jsonResponse({ artifacts: [] }));
            return Promise.reject(new Error(`Unexpected request: ${path}`));
          }),
        ),
      );
      render(<DesktopWorkspace />);
      const composer = screen.getByRole("textbox", {
        name: "Analysis request",
      });
      await user.type(composer, "How many rooms?");
      await user.click(screen.getByRole("button", { name: "Send message" }));
      expect(await screen.findByText("First answer.")).toBeVisible();
      if (mode === "deep analysis") {
        await user.click(screen.getByRole("button", { name: "Add context" }));
        await user.click(
          screen.getByRole("menuitem", { name: "Deep analysis" }),
        );
      }
      await user.type(composer, "How many patients?");
      await user.click(screen.getByRole("button", { name: "Send message" }));
      await waitFor(() => {
        expect(window.location.search).toContain("task_questions_12345678");
      });
      await user.click(screen.getByRole("button", { name: "Provenance" }));
      const secondGroup = await screen.findByRole("group", {
        name: "How many patients?",
      });
      const firstGroup = screen.getByRole("group", { name: "How many rooms?" });
      expect(within(firstGroup).getAllByText(firstQuery.query)).toHaveLength(1);
      expect(
        within(firstGroup).queryByText(secondQuery.query),
      ).not.toBeInTheDocument();
      expect(within(secondGroup).getByText(secondQuery.query)).toBeVisible();
      expect(
        screen.queryByRole("group", { name: /Question msg_/ }),
      ).not.toBeInTheDocument();

      const newChat = screen.getAllByRole("button", { name: "New chat" })[0];
      if (!newChat) throw new Error("New chat control is unavailable");
      await user.click(newChat);
      await user.click(screen.getByRole("button", { name: "Provenance" }));
      expect(screen.getByText("No queries yet.")).toBeVisible();
      expect(screen.queryByText(firstQuery.query)).not.toBeInTheDocument();
      expect(screen.queryByText(secondQuery.query)).not.toBeInTheDocument();
    },
  );

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

  it("retains failed attempts under their question after a later turn", async () => {
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
            return Promise.resolve(
              jsonResponse(
                {
                  messageId:
                    messageCount === 1
                      ? firstQuery.messageId
                      : secondQuery.messageId,
                },
                201,
              ),
            );
          }
          if (path.endsWith("/chat"))
            return Promise.resolve(
              messageCount === 1
                ? eventResponse([
                    [
                      "data_step",
                      {
                        ...firstStep,
                        state: "failed",
                        detail: "Query rejected",
                      },
                    ],
                    ["failed", { message: "Unable to answer" }],
                  ])
                : eventResponse([
                    ["delta", { text: "Second answer." }],
                    ["completed", { messageId: "msg_reply_second_12345678" }],
                  ]),
            );
          return Promise.reject(new Error(`Unexpected request: ${path}`));
        }),
      ),
    );
    render(<DesktopWorkspace />);
    const composer = screen.getByRole("textbox", { name: "Analysis request" });
    await user.type(composer, "How many rooms?");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(screen.getAllByText("Unable to answer").length).toBeGreaterThan(0);
    });
    await user.type(composer, "Another question");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Second answer.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    const group = screen.getByRole("group", { name: "How many rooms?" });
    expect(within(group).getByText("Failed query")).toBeVisible();
    expect(within(group).getByText(firstQuery.query)).toBeVisible();
  });
});
