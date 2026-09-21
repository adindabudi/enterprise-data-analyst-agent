import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { DataStepsBlock, readDataStep, type DataStep } from "./DataSteps";

afterEach(cleanup);

describe("readDataStep", () => {
  it("reads a reported step and counts its rows as a number", () => {
    expect(
      readDataStep({
        stepId: "step-1",
        kind: "gql",
        label: "Queried lamna-healthcare graph",
        state: "completed",
        query: "MATCH (r:rooms) RETURN count(*) AS total",
        source: "lamna-healthcare",
        rowCount: "24",
        querySha256: "a".repeat(64),
      }),
    ).toEqual({
      stepId: "step-1",
      kind: "gql",
      label: "Queried lamna-healthcare graph",
      state: "completed",
      query: "MATCH (r:rooms) RETURN count(*) AS total",
      source: "lamna-healthcare",
      rowCount: 24,
      querySha256: "a".repeat(64),
    });
  });

  it("drops a step that does not name what it read", () => {
    expect(readDataStep({ stepId: "step-1", kind: "gql" })).toBeNull();
  });
});

const gqlStep: DataStep = {
  stepId: "step-1",
  kind: "gql",
  label: "Queried lamna-healthcare graph",
  state: "completed",
  query: "MATCH (r:rooms) RETURN count(*) AS total",
  source: "lamna-healthcare",
  rowCount: 1,
  querySha256: "a".repeat(64),
};

const searchStep: DataStep = {
  stepId: "step-2",
  kind: "ontology_search",
  label: "Searched lamna-healthcare ontology",
  state: "completed",
  query: "How many patients are admitted?",
  source: "lamna-healthcare",
  querySha256: "b".repeat(64),
};

describe("DataStepsBlock", () => {
  it("labels truncated failed requests without pretending the displayed text is complete", () => {
    render(
      <DataStepsBlock
        steps={[{ ...gqlStep, state: "failed", queryTruncated: true }]}
        running={false}
      />,
    );
    expect(
      screen.getByText(/Request text is truncated for display/),
    ).toBeVisible();
  });

  it("summarises a finished turn instead of listing every step at once", () => {
    render(<DataStepsBlock steps={[gqlStep, searchStep]} running={false} />);

    expect(
      screen.getByRole("button", { name: /Analyzed · 2 data steps/ }),
    ).toBeVisible();
    expect(screen.queryByText(gqlStep.label)).not.toBeInTheDocument();
  });

  it("shows the steps while the turn is still running", () => {
    render(<DataStepsBlock steps={[gqlStep]} running={true} />);

    expect(screen.getByText(gqlStep.label)).toBeVisible();
  });

  it("keeps a summary open once the reader opened it", async () => {
    render(<DataStepsBlock steps={[gqlStep]} running={false} />);

    await userEvent.click(screen.getByRole("button", { name: /Analyzed/ }));

    expect(screen.getByText(gqlStep.label)).toBeVisible();
  });

  it("reveals the exact statement that ran only when a step is opened", async () => {
    render(<DataStepsBlock steps={[gqlStep]} running={true} />);

    expect(screen.queryByText(gqlStep.query)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: gqlStep.label }));

    expect(screen.getByText(gqlStep.query)).toBeVisible();
    expect(screen.getByText(/1 row/)).toBeVisible();
  });

  it("says an ontology search sent a question rather than a query", async () => {
    render(<DataStepsBlock steps={[searchStep]} running={true} />);

    await userEvent.click(
      screen.getByRole("button", { name: searchStep.label }),
    );

    // Fabric translates this itself and never returns the query, so claiming one would be invented.
    expect(screen.getByText(/not returned to this app/)).toBeVisible();
    expect(screen.getByText(searchStep.query)).toBeVisible();
  });

  it("leaves a failed turn open with the reason it failed", () => {
    render(
      <DataStepsBlock
        steps={[
          { ...gqlStep, state: "failed", detail: "the endpoint returned 400" },
        ]}
        running={false}
      />,
    );

    expect(screen.getByText(gqlStep.label)).toBeVisible();
    expect(screen.getByText("the endpoint returned 400")).toBeVisible();
  });

  it("renders nothing when a turn read no data at all", () => {
    const { container } = render(<DataStepsBlock steps={[]} running={false} />);

    expect(container).toBeEmptyDOMElement();
  });
});
