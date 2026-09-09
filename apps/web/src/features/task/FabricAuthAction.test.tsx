import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FabricAuthAction } from "./FabricAuthAction";

describe("FabricAuthAction", () => {
  afterEach(() => {
    cleanup();
    sessionStorage.clear();
  });

  it("starts only after an explicit click and navigates to a validated Microsoft URL", async () => {
    const start = vi
      .fn()
      .mockResolvedValue(
        "https://login.microsoftonline.com/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/oauth2/v2.0/authorize",
      );
    const navigate = vi.fn();

    render(
      <FabricAuthAction
        actionPath="/api/fabric/auth/start"
        navigate={navigate}
        start={start}
        taskId="task_12345678"
      />,
    );

    expect(start).not.toHaveBeenCalled();
    expect(screen.queryByText(/login\.microsoftonline\.com/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect to Fabric" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith("task_12345678");
      expect(navigate).toHaveBeenCalledTimes(1);
    });
    expect(sessionStorage.getItem("eda.fabric.task")).toBe("task_12345678");
  });

  it("renders generic feedback and never navigates for an invalid authorization URL", async () => {
    const start = vi
      .fn()
      .mockRejectedValue(new Error("authorization URL is invalid"));
    const navigate = vi.fn();
    render(
      <FabricAuthAction
        actionPath="/api/fabric/auth/start"
        navigate={navigate}
        start={start}
        taskId="task_12345678"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Connect to Fabric" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Connection could not start.",
    );
    expect(navigate).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("authorization URL");
  });
});
