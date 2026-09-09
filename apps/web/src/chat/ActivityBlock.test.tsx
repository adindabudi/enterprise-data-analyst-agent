import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  ActivityBlock,
  activityStatus,
  withLatestActivity,
} from "./ActivityBlock";

afterEach(cleanup);

describe("ActivityBlock", () => {
  it("shows an animated progress indicator while an activity is running", () => {
    render(
      <ActivityBlock
        activity={{
          id: "fabric-query",
          label: "Querying Lamna healthcare operations ontology",
          status: "running",
        }}
      />,
    );

    expect(
      screen.getByRole("progressbar", { name: "Query in progress" }),
    ).toBeVisible();
  });

  it("does not show the progress indicator after completion", () => {
    render(
      <ActivityBlock
        activity={{
          id: "fabric-query",
          label: "Query complete",
          status: "completed",
        }}
      />,
    );

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("reports how long a finished activity took", () => {
    render(
      <ActivityBlock
        activity={{
          id: "fabric-query",
          label: "Query complete",
          status: "completed",
        }}
        startedAt={Date.now() - 12_000}
      />,
    );

    expect(screen.getByText("in 12s")).toBeVisible();
  });

  it("reports elapsed time while an activity is still running", () => {
    render(
      <ActivityBlock
        activity={{
          id: "fabric-query",
          label: "Querying",
          status: "running",
        }}
        startedAt={Date.now() - 75_000}
      />,
    );

    expect(screen.getByText("1m 15s elapsed")).toBeVisible();
  });

  it("omits timing when the turn start is unknown", () => {
    render(
      <ActivityBlock
        activity={{ id: "fabric-query", label: "Query", status: "completed" }}
      />,
    );

    expect(screen.queryByText(/elapsed|^in |^after /)).not.toBeInTheDocument();
  });
});

describe("activityStatus", () => {
  it("takes the outcome the stream stated", () => {
    expect(activityStatus("failed")).toBe("failed");
    expect(activityStatus("completed")).toBe("completed");
  });

  it("treats an absent or unknown outcome as still running", () => {
    // An older API sends no state at all, and a turn in flight must not read as finished.
    expect(activityStatus(undefined)).toBe("running");
    expect(activityStatus("something-new")).toBe("running");
  });
});

describe("withLatestActivity", () => {
  it("replaces the activity in flight", () => {
    const first = withLatestActivity(
      [],
      "Querying the configured source",
      "running",
      "Started.",
    );
    const second = withLatestActivity(
      first,
      "Agent is responding",
      undefined,
      undefined,
    );

    expect(second).toHaveLength(1);
    expect(second[0]?.label).toBe("Agent is responding");
  });

  it("keeps a failure on screen when the next milestone arrives", () => {
    // A capacity that is paused fails one tool and the turn carries on; overwriting it hides the cause.
    const failed = withLatestActivity(
      [],
      "Querying the configured source",
      "failed",
      "returned 404: CapacityNotActive",
    );
    const next = withLatestActivity(
      failed,
      "Agent is thinking",
      undefined,
      undefined,
    );

    expect(next.map((activity) => activity.status)).toEqual([
      "failed",
      "running",
    ]);
    expect(next[0]?.detail).toBe("returned 404: CapacityNotActive");
    expect(new Set(next.map((activity) => activity.id)).size).toBe(2);
  });
});
