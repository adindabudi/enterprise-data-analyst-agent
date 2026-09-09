import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Conversation } from "./Conversation";
import type { DataStep } from "./DataSteps";

afterEach(cleanup);

const step: DataStep = {
  stepId: "step-1",
  kind: "gql",
  label: "Queried lamna-healthcare graph",
  state: "completed",
  query: "MATCH (r:rooms) RETURN count(*) AS total",
  source: "lamna-healthcare",
  rowCount: 1,
  querySha256: "a".repeat(64),
};

describe("conversation", () => {
  it("renders narrative messages and typed activity without raw event JSON", () => {
    render(
      <Conversation
        messages={[
          {
            id: "msg_1",
            role: "assistant",
            text: "Variance is concentrated in APAC.",
          },
        ]}
        activities={[
          {
            id: "evt_1",
            label: "Validated workbook controls",
            status: "completed",
          },
        ]}
      />,
    );

    expect(screen.getByText("Variance is concentrated in APAC.")).toBeVisible();
    expect(screen.getByText("Validated workbook controls")).toBeVisible();
    expect(screen.queryByText("evt_1")).not.toBeInTheDocument();
  });

  it("keeps the data steps with the answer they produced", () => {
    render(
      <Conversation
        messages={[
          { id: "msg_1", role: "user", text: "berapa kamar?" },
          { id: "msg_2", role: "assistant", text: "24 kamar.", steps: [step] },
        ]}
        activities={[]}
      />,
    );

    // Read afterwards, an answer is only checkable if its own reads sit with it.
    const answer = screen
      .getByRole("region", { name: "Data steps" })
      .closest("article");
    expect(answer).toHaveTextContent("24 kamar.");
    expect(
      screen.getByRole("button", { name: /Analyzed · 1 data step/ }),
    ).toBeVisible();
  });

  it("shows the steps of the turn still in flight", () => {
    render(
      <Conversation
        messages={[]}
        activities={[]}
        liveSteps={[{ ...step, state: "running" }]}
      />,
    );

    expect(screen.getByRole("region", { name: "Data steps" })).toBeVisible();
    expect(screen.getByText(step.label)).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "Reading the source" }),
    ).toBeVisible();
  });

  it.each(["completed", "failed"] as const)(
    "stops source loading when the live query is %s",
    (state) => {
      render(
        <Conversation
          messages={[]}
          activities={[]}
          liveSteps={[{ ...step, state }]}
        />,
      );

      expect(
        screen.queryByRole("progressbar", { name: "Reading the source" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /Analyzed · 1 data step/ }),
      ).toBeVisible();
    },
  );
});
