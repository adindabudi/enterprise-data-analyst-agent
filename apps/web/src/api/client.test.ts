import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiRequestError } from "./client";

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("keeps the problem body so a blocked readiness still reports healthy packs", async () => {
    const packs = { core: "ready", fabric: "configured", documents: "failed" };
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          respond(503, { status: "blocked", featurePacks: packs }),
        ),
      ),
    );

    const error = await new ApiClient()
      .get("/health/ready")
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(503);
    expect((error as ApiRequestError).body).toEqual({
      status: "blocked",
      featurePacks: packs,
    });
  });

  it("leaves the body undefined when the response is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("gateway", { status: 502 }))),
    );

    const error = await new ApiClient()
      .get("/health/ready")
      .catch((caught: unknown) => caught);

    expect((error as ApiRequestError).body).toBeUndefined();
  });
});
