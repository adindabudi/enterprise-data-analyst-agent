import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { installMatchMedia } from "../test/match-media";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("auth bootstrap", () => {
  it("keeps a nonblank loading status while the session request is pending", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => undefined)),
    );

    render(<App />);

    expect(
      screen.getByRole("progressbar", { name: /preparing your session/i }),
    ).toBeVisible();
  });

  it("renders explicit sign in after a 401 without navigating", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: /sign in/i }),
    ).toBeVisible();
  });

  it("mounts the one-view mobile workspace after authentication", async () => {
    installMatchMedia({ "(max-width: 1023px)": true });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "authenticated" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: "Analyses" }),
    ).toBeVisible();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("projects configured ontology login into the product bar", async () => {
    installMatchMedia({ "(max-width: 1023px)": false });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        const body = path.endsWith("/health/ready")
          ? {
              status: "ready",
              featurePacks: {
                core: "ready",
                fabric: "configured",
                documents: "disabled",
                powerBiProject: "disabled",
              },
            }
          : path.endsWith("/api/fabric/auth/status")
            ? { provider: "ontology", state: "unlinked", chatQuery: false }
            : { status: "authenticated" };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("button", { name: "Connect Fabric" }),
    ).toBeVisible();
  });

  it("reports a paused capacity instead of a live connection", async () => {
    installMatchMedia({ "(max-width: 1023px)": false });
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        requested.push(path);
        const body = path.endsWith("/health/ready")
          ? { status: "ready", featurePacks: { fabric: "configured" } }
          : path.endsWith("/api/fabric/auth/status")
            ? { provider: "ontology", state: "linked", chatQuery: true }
            : path.endsWith("/api/fabric/source/status")
              ? { capacity: "paused" }
              : { status: "authenticated" };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    render(<App />);

    expect(await screen.findByText("Fabric capacity paused")).toBeVisible();
    expect(screen.queryByText("Fabric connected for chat")).toBeNull();
    expect(requested).toContain("/api/fabric/source/status");
  });
});
