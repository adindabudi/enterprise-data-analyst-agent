import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer, type ComposerRequest } from "./Composer";

afterEach(cleanup);

describe("composer", () => {
  it("does not expose task controls while idle", () => {
    render(<Composer disabled={false} />);

    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Steer current task" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Ask" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Analyze" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add context" })).toBeEnabled();
  });

  it("sends in automatic mode by default", async () => {
    const user = userEvent.setup();
    const calls: Array<[string, boolean]> = [];
    render(
      <Composer
        disabled={false}
        onSend={(message, request) =>
          calls.push([message, request.deepAnalysis])
        }
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Build a workbook",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(calls).toEqual([["Build a workbook", false]]);
  });

  it("offers deep analysis as a contextual one-turn override", async () => {
    const user = userEvent.setup();
    const calls: Array<[string, boolean]> = [];
    render(
      <Composer
        disabled={false}
        onSend={(message, request) =>
          calls.push([message, request.deepAnalysis])
        }
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add context" }));
    await user.click(screen.getByRole("menuitem", { name: "Deep analysis" }));
    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Build a workbook",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(screen.queryByText("Deep analysis enabled")).not.toBeInTheDocument();
    expect(calls).toEqual([["Build a workbook", true]]);
  });

  it("selects deep analysis only after the controlled upload is clean", async () => {
    const user = userEvent.setup();
    const calls: Array<[string, boolean]> = [];
    const onSend = (message: string, request: ComposerRequest): void => {
      calls.push([message, request.deepAnalysis]);
    };
    const onAttach = vi.fn();
    const { rerender } = render(
      <Composer disabled={false} onSend={onSend} onAttach={onAttach} />,
    );

    await user.click(screen.getByRole("button", { name: "Add context" }));
    await user.click(screen.getByRole("menuitem", { name: "Attach file" }));

    fireEvent.change(screen.getByLabelText("Choose analysis input"), {
      target: {
        files: [new File(["data"], "revenue.csv", { type: "text/csv" })],
      },
    });
    expect(onAttach).toHaveBeenCalledOnce();
    const attachment = {
      displayName: "revenue.csv",
      sessionId: "ses_upload_12345678",
      uploadId: "upl_upload_12345678",
    };
    rerender(
      <Composer
        disabled={false}
        onSend={onSend}
        onAttach={onAttach}
        attachment={{ ...attachment, state: "scanning" }}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Summarize this file",
    );
    await user.keyboard("{Enter}");
    expect(calls).toEqual([]);
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    rerender(
      <Composer
        disabled={false}
        onSend={onSend}
        onAttach={onAttach}
        attachment={{ ...attachment, state: "clean" }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(calls).toEqual([["Summarize this file", true]]);
  });

  it("keeps drafting and stop available while one agent turn is running", async () => {
    const user = userEvent.setup();
    render(<Composer disabled running onStop={() => undefined} />);

    const input = screen.getByRole("textbox", { name: "Analysis request" });
    expect(input).toBeEnabled();
    await user.type(input, "Draft the next question");
    expect(input).toHaveValue("Draft the next question");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Stop current task" }),
    ).toBeEnabled();
  });

  it("sends into a run instead of making the reader wait for it", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    render(
      <Composer
        disabled={false}
        running
        onSend={(message) => calls.push(message)}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Also include occupancy",
    );
    const send = screen.getByRole("button", { name: "Send message" });
    // The turn already going is the one that should hear this, so the button stays live.
    expect(send).toBeEnabled();
    await user.click(send);

    expect(calls).toEqual(["Also include occupancy"]);
  });
});
