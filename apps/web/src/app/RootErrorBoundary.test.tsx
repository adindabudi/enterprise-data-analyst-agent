import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RootErrorBoundary } from "./RootErrorBoundary";

function Exploding(): never {
  throw new Error("render blew up");
}

describe("RootErrorBoundary", () => {
  afterEach(async () => {
    cleanup();
    await new Promise<void>((resolve) => {
      queueMicrotask(resolve);
    });
    vi.restoreAllMocks();
  });

  it("reports the crash it hides behind the fallback", () => {
    const reported = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    render(
      <RootErrorBoundary>
        <Exploding />
      </RootErrorBoundary>,
    );

    expect(screen.getByText("Workspace unavailable")).toBeInTheDocument();
    // React swallows the error once a boundary handles it, so an empty handler left no trace at all.
    expect(
      reported.mock.calls.some(
        (call) => call[0] === "workspace crashed" && call[1] instanceof Error,
      ),
    ).toBe(true);
  });
});
