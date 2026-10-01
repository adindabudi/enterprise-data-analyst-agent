import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { blockedUploadMessage, Composer } from "./Composer";

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

  it("sends through the durable task path by default", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    render(
      <Composer disabled={false} onSend={(message) => calls.push(message)} />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Build a workbook",
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(calls).toEqual(["Build a workbook"]);
  });

  it("only offers file attachment as additional context", async () => {
    const user = userEvent.setup();
    render(<Composer disabled={false} onAttach={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Add context" }));

    expect(screen.getByRole("menuitem", { name: "Attach file" })).toBeVisible();
    expect(screen.queryByText("Deep analysis")).not.toBeInTheDocument();
  });

  it("sends only after the controlled upload is clean", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const onSend = (message: string): void => {
      calls.push(message);
    };
    const onAttach = vi.fn();
    const onBlockedUploadSend = vi.fn();
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
        onBlockedUploadSend={onBlockedUploadSend}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Analysis request" }),
      "Summarize this file",
    );
    await user.keyboard("{Enter}");
    expect(calls).toEqual([]);
    expect(onBlockedUploadSend).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("button", {
        name: "Wait for the file scan to finish",
      }),
    ).toBeDisabled();
    rerender(
      <Composer
        disabled={false}
        onSend={onSend}
        onAttach={onAttach}
        attachment={{ ...attachment, state: "clean" }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(calls).toEqual(["Summarize this file"]);
  });

  it("tells the user to remove a file that failed its scan instead of waiting", () => {
    const attachment = {
      displayName: "revenue.xlsx",
      uploadId: "upl_12345678",
    };

    for (const state of ["pending", "uploading", "scanning"] as const) {
      expect(blockedUploadMessage({ ...attachment, state })).toBe(
        "Wait for the file scan to finish",
      );
    }
    for (const state of ["rejected", "scan_failed", "error"] as const) {
      expect(blockedUploadMessage({ ...attachment, state })).toBe(
        "Remove the file to send without it",
      );
    }
    render(
      <Composer
        disabled={false}
        attachment={{ ...attachment, state: "rejected" }}
      />,
    );

    expect(
      screen.getByRole("button", {
        name: "Remove the file to send without it",
      }),
    ).toBeDisabled();
  });

  it("keeps drafting and stop available while one agent turn is running", async () => {
    const user = userEvent.setup();
    render(<Composer disabled running onStop={() => undefined} />);

    const input = screen.getByRole("textbox", { name: "Analysis request" });
    expect(input).toBeEnabled();
    await user.type(input, "Draft the next question");
    expect(input).toHaveValue("Draft the next question");
    expect(
      screen.getByRole("button", { name: "Send to the running analysis" }),
    ).toBeDisabled();
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
    const send = screen.getByRole("button", {
      name: "Send to the running analysis",
    });
    // The turn already going is the one that should hear this, so the button stays live.
    expect(send).toBeEnabled();
    await user.click(send);

    expect(calls).toEqual(["Also include occupancy"]);
  });
});
