import { FluentProvider } from "@fluentui/react-components";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { DEFAULT_BRANDING } from "../app/branding";
import { compileTheme } from "../app/theme";
import { DesktopWorkspace } from "./DesktopWorkspace";
import { MobileWorkspace } from "./MobileWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  document.cookie = "eda_csrf=; Max-Age=0; Path=/";
});

it.each([
  ["desktop", DesktopWorkspace],
  ["mobile", MobileWorkspace],
] as const)(
  "%s restores conversation and previous task files from the server after remount",
  async (layout, Workspace) => {
    const user = userEvent.setup();
    const sessionId = "ses_persisted_12345678";
    const timestamp = "2026-09-08T00:00:00Z";
    const session = {
      sessionId,
      title: "Saved occupancy",
      lastActivityAt: timestamp,
    };
    const tasks = ["task_first_12345678", "task_second_12345678"].map(
      (taskId) => ({
        taskId,
        status: "completed",
        sourceMessageId: "msg_question_12345678",
        finalMessageId: null,
        createdAt: timestamp,
        updatedAt: timestamp,
      }),
    );
    window.history.replaceState(null, "", `/?session=${sessionId}`);
    document.cookie = "eda_csrf=test-value; Path=/";
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) => {
        const path = input instanceof Request ? input.url : input.toString();
        const artifactTaskId = path.split("/")[3] ?? "";
        const body =
          path === "/api/sessions"
            ? [session]
            : path.endsWith("/history")
              ? {
                  messages: [
                    {
                      messageId: "msg_question_12345678",
                      role: "user",
                      text: "Export occupancy",
                      createdAt: timestamp,
                      taskId: null,
                    },
                    {
                      messageId: "msg_answer_12345678",
                      role: "assistant",
                      text: "Both workbooks are ready",
                      createdAt: timestamp,
                      taskId: tasks[1]?.taskId,
                    },
                  ],
                  tasks,
                }
              : path.endsWith("/todos")
                ? {
                    items: [
                      {
                        id: 1,
                        title: "Publish workbook",
                        description: null,
                        isComplete: true,
                      },
                    ],
                  }
                : path.endsWith("/messages")
                  ? { messageId: "msg_next_12345678" }
                  : path.endsWith("/tasks")
                    ? { taskId: "task_followup_12345678" }
                    : path.endsWith("/provenance")
                      ? { sourceQueries: [], artifacts: [] }
                      : path.endsWith("/artifacts")
                        ? {
                            artifacts: [
                              {
                                artifactId: `artifact-${artifactTaskId}`,
                                version: 2,
                                kind: "xlsx",
                                sha256: "a".repeat(64),
                                sizeBytes: 2048,
                                displayName: `${artifactTaskId}.xlsx`,
                              },
                            ],
                          }
                        : session;
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    const workspace = (
      <FluentProvider theme={compileTheme(DEFAULT_BRANDING)}>
        <Workspace />
      </FluentProvider>
    );
    const firstMount = render(workspace);
    expect(
      await screen.findByRole("heading", { name: "Saved occupancy" }),
    ).toBeVisible();
    expect(await screen.findByText("Both workbooks are ready")).toBeVisible();
    if (layout === "desktop") {
      expect(await screen.findByText("task_first_12345678.xlsx")).toBeVisible();
      expect(
        await screen.findByText("task_second_12345678.xlsx"),
      ).toBeVisible();
    }
    expect(
      screen.queryByRole("tab", { name: "To-do" }),
    ).not.toBeInTheDocument();
    if (layout === "desktop")
      expect(
        await screen.findByRole("button", { name: /^Tasks/ }),
      ).toHaveAttribute("aria-expanded", "true");
    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Explain the totals",
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Send message" }),
      ).toBeEnabled();
    });
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => {
      expect(window.location.search).toContain("task_followup_12345678");
    });
    if (layout === "desktop")
      expect(screen.getByText("task_first_12345678.xlsx")).toBeVisible();
    expect(window.location.search).toContain(`session=${sessionId}`);

    firstMount.unmount();
    render(workspace);
    expect(await screen.findByText("Both workbooks are ready")).toBeVisible();
    if (layout === "mobile") {
      expect(
        screen.getByRole("heading", { name: "Saved occupancy" }),
      ).toBeVisible();
    } else {
      await waitFor(() =>
        expect(
          within(
            screen.getByRole("navigation", { name: "Analyses" }),
          ).getByRole("button", { name: "Saved occupancy" }),
        ).toBeVisible(),
      );
    }
    if (layout === "desktop")
      expect(await screen.findByText("task_first_12345678.xlsx")).toBeVisible();
  },
);
