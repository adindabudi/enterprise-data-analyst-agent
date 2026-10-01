import { describe, expect, it } from "vitest";

import {
  CAPACITY_PAUSED_DETAIL,
  ontologySourceStatus,
  queryableSource,
  type FabricSourceContext,
} from "./source-context";

const linked = {
  provider: "ontology",
  state: "linked",
  chatQuery: true,
} as const;

describe("ontology source status", () => {
  it.each([
    [{ availability: "configured", authorization: linked }, true],
    [{ availability: "ready", authorization: linked }, true],
    [
      {
        availability: "configured",
        authorization: { ...linked, chatQuery: false },
      },
      false,
    ],
    [
      {
        availability: "ready",
        authorization: { ...linked, state: "reauth_required" },
      },
      false,
    ],
    [{ availability: "failed", authorization: linked }, false],
  ] as const)("knows when %j can answer queries", (context, expected) => {
    expect(queryableSource(context)).toBe(expected);
  });

  it.each(["configured", "ready"] as const)(
    "reports a paused capacity on a %s source",
    (availability) => {
      expect(
        ontologySourceStatus({
          availability,
          authorization: linked,
          capacity: "paused",
        }),
      ).toBe(CAPACITY_PAUSED_DETAIL);
    },
  );

  it.each([
    [
      { availability: "configured", authorization: linked, capacity: "active" },
      "Connected; acceptance pending",
    ],
    [
      { availability: "ready", authorization: linked, capacity: "checking" },
      "Connected and ready",
    ],
    [
      {
        availability: "configured",
        authorization: { ...linked, chatQuery: false },
        capacity: "paused",
      },
      "Signed in; not ready for queries",
    ],
    [
      {
        availability: "configured",
        authorization: { ...linked, state: "unlinked" },
        capacity: "paused",
      },
      "Sign in required",
    ],
  ] satisfies [FabricSourceContext, string][])(
    "keeps %j as %s",
    (context, expected) => {
      expect(ontologySourceStatus(context)).toBe(expected);
    },
  );
});
