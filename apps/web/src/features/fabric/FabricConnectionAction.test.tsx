import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FabricConnectionAction } from "./FabricConnectionAction";

afterEach(cleanup);

describe("FabricConnectionAction", () => {
  it("renders nothing while Fabric is disabled", () => {
    const { container } = render(
      <FabricConnectionAction availability="disabled" />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders a noninteractive unavailable state when Fabric failed", () => {
    render(<FabricConnectionAction availability="failed" />);

    expect(screen.getByText("Fabric unavailable")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not claim a connection while a linked source cannot be queried", () => {
    render(
      <FabricConnectionAction
        availability="configured"
        authorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: false,
        }}
      />,
    );

    expect(screen.getByText("Fabric not ready")).toBeVisible();
    expect(screen.queryByText("Fabric connected")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("reports a chat-only connection when a configured source answers chat", () => {
    render(
      <FabricConnectionAction
        availability="configured"
        authorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
        }}
      />,
    );

    expect(screen.getByText("Fabric connected for chat")).toBeVisible();
    expect(screen.queryByText("Fabric not ready")).toBeNull();
  });

  it("starts an explicit taskless login and navigates to Entra", async () => {
    const start = vi
      .fn()
      .mockResolvedValue(
        "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/oauth2/v2.0/authorize",
      );
    const navigate = vi.fn();
    render(
      <FabricConnectionAction
        availability="configured"
        authorization={{
          provider: "ontology",
          state: "unlinked",
          chatQuery: false,
        }}
        navigate={navigate}
        start={start}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Connect Fabric" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith();
      expect(navigate).toHaveBeenCalledTimes(1);
    });
  });

  it.each([
    ["linked", "Fabric connected"],
    ["reauth_required", "Reconnect Fabric"],
  ] as const)("renders %s owner state", (state, label) => {
    render(
      <FabricConnectionAction
        availability="ready"
        authorization={{ provider: "ontology", state, chatQuery: true }}
      />,
    );

    expect(screen.getByText(label)).toBeVisible();
  });

  it.each(["configured", "ready"] as const)(
    "reports a paused capacity instead of a %s connection",
    (availability) => {
      render(
        <FabricConnectionAction
          availability={availability}
          authorization={{
            provider: "ontology",
            state: "linked",
            chatQuery: true,
          }}
          capacity="paused"
        />,
      );

      expect(screen.getByText("Fabric capacity paused")).toBeVisible();
      expect(screen.queryByText(/Fabric connected/)).toBeNull();
      expect(screen.queryByRole("button")).toBeNull();
    },
  );

  it("does not claim a connection before the capacity has been read", () => {
    render(
      <FabricConnectionAction
        availability="configured"
        authorization={{
          provider: "ontology",
          state: "linked",
          chatQuery: true,
        }}
        capacity="checking"
      />,
    );

    expect(screen.getByText("Checking Fabric…")).toBeVisible();
    expect(screen.queryByText(/Fabric connected/)).toBeNull();
  });

  it.each(["active", "unknown"] as const)(
    "keeps the connection label when the capacity is %s",
    (capacity) => {
      render(
        <FabricConnectionAction
          availability="configured"
          authorization={{
            provider: "ontology",
            state: "linked",
            chatQuery: true,
          }}
          capacity={capacity}
        />,
      );

      expect(screen.getByText("Fabric connected for chat")).toBeVisible();
    },
  );

  it("leaves sign-in states alone whatever the capacity", () => {
    render(
      <FabricConnectionAction
        availability="configured"
        authorization={{
          provider: "ontology",
          state: "reauth_required",
          chatQuery: true,
        }}
        capacity="paused"
      />,
    );

    expect(
      screen.getByRole("button", { name: "Reconnect Fabric" }),
    ).toBeVisible();
    expect(screen.queryByText("Fabric capacity paused")).toBeNull();
  });
});
