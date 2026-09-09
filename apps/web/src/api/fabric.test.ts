import { afterEach, describe, expect, it, vi } from "vitest";

import {
  completeFabricAuthorization,
  getFabricAuthorizationStatus,
  startFabricAuthorization,
} from "./fabric";

describe("Fabric API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "eda_csrf=; Max-Age=0; Path=/";
  });

  it("sends CSRF and validates the returned Entra navigation URL", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          authorizationUrl:
            "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/oauth2/v2.0/authorize",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const url = await startFabricAuthorization("task_12345678");

    expect(url).toContain("https://login.microsoftonline.com/");
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    if (!call) throw new Error("Fabric start request was not sent");
    const [path, request] = call;
    expect(path).toBe("/api/fabric/auth/start");
    expect(request?.method).toBe("POST");
    expect(request?.credentials).toBe("same-origin");
    expect(new Headers(request?.headers).get("X-CSRF-Token")).toBe(
      "csrf-value",
    );
    expect(request?.body).toBe(JSON.stringify({ taskId: "task_12345678" }));
  });

  it("starts taskless login and reads a strict provider-aware status", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            authorizationUrl:
              "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/oauth2/v2.0/authorize",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            provider: "ontology",
            state: "unlinked",
            chatQuery: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await startFabricAuthorization();
    await expect(getFabricAuthorizationStatus()).resolves.toEqual({
      provider: "ontology",
      state: "unlinked",
      chatQuery: false,
    });

    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({}));
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/fabric/auth/status");
  });

  it.each([
    ["logistics", "Logistics shipment ontology"],
    ["energy", "Energy consumption ontology"],
    ["unicode", `${"a".repeat(239)}\u{20000}`],
  ])("reads configured source metadata for %s", async (alias, description) => {
    const status = {
      provider: "ontology",
      state: "unlinked",
      chatQuery: false,
      source: { alias, description },
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(JSON.stringify(status))),
    );

    await expect(getFabricAuthorizationStatus()).resolves.toEqual(status);
  });

  it.each([
    null,
    {},
    { alias: "logistics", description: "a".repeat(241) },
    { alias: "logistics", description: " " },
    { alias: "logistics", description: 42 },
    { alias: "INVALID_ALIAS", description: "Logistics shipment ontology" },
    {
      alias: "logistics",
      description: "Logistics shipment ontology",
      ontologyId: "private-target",
    },
    {
      alias: "logistics",
      description: "Logistics shipment ontology",
      endpoint: "https://private.example",
    },
  ])("rejects invalid or nonpublic source metadata %#", async (source) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            provider: "ontology",
            state: "linked",
            chatQuery: true,
            source,
          }),
        ),
      ),
    );

    await expect(getFabricAuthorizationStatus()).rejects.toThrow(
      "Fabric authorization status is invalid",
    );
  });

  it("rejects non-Microsoft URLs and completes with an empty CSRF-protected POST", async () => {
    document.cookie = "eda_csrf=csrf-value; Path=/";
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ authorizationUrl: "https://evil.example/login" }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(startFabricAuthorization("task_12345678")).rejects.toThrow();
    await completeFabricAuthorization();

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/fabric/auth/complete",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": "csrf-value" },
      }),
    );
  });
});
