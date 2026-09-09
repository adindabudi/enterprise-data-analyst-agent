import { withSessionHistory } from "../test/session-history";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopWorkspace } from "./DesktopWorkspace";

const artifact = {
  artifactId: "artifact-preview_12345678",
  version: 2,
  kind: "html",
  sha256: "a".repeat(64),
  displayName: "analysis.html",
  sizeBytes: 2048,
};
const html =
  "<h1>Regional revenue</h1><script>document.body.dataset.ready = 'true'</script>";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function artifactResponse(): Response {
  return new Response(JSON.stringify({ artifacts: [artifact] }), {
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe("authenticated HTML preview", () => {
  it("fetches only after Preview and uses the existing restricted sandbox", async () => {
    const user = userEvent.setup();
    const content = deferredResponse();
    const fetchMock = vi.fn<typeof fetch>((input) =>
      requestUrl(input).endsWith("/content")
        ? content.promise
        : Promise.resolve(artifactResponse()),
    );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    window.history.replaceState(null, "", "/?task=task_preview_12345678");
    render(<DesktopWorkspace />);

    expect(await screen.findByText(artifact.displayName)).toBeVisible();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestUrl(input).endsWith("/content"),
      ),
    ).toBe(false);
    const trigger = await screen.findByRole("button", {
      name: "Preview analysis.html",
    });
    expect(trigger).toBeVisible();
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", {
      name: "Preview analysis.html",
    });
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "Loading preview",
    );
    expect(
      within(dialog).queryByTitle("Artifact preview"),
    ).not.toBeInTheDocument();
    const contentCall = fetchMock.mock.calls.find(([input]) =>
      requestUrl(input).endsWith("/content"),
    );
    expect(contentCall?.[0]).toBe(
      "/api/tasks/task_preview_12345678/artifacts/artifact-preview_12345678/versions/2/content",
    );
    expect(contentCall?.[1]?.credentials).toBe("same-origin");
    expect(new Headers(contentCall?.[1]?.headers).get("Accept")).toBe(
      "text/html",
    );
    await act(async () => {
      content.resolve(
        new Response(html, { headers: { "Content-Type": "text/html" } }),
      );
      await content.promise;
    });
    const frame = within(dialog).getByTitle("Artifact preview");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame.getAttribute("srcdoc")).toContain("connect-src 'none'");
    expect(frame.getAttribute("srcdoc")).toContain("default-src 'none'");
    expect(frame.getAttribute("srcdoc")).toContain(html);
    await user.click(
      within(dialog).getByRole("button", { name: "Close preview" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it.each(["unauthorized", "network failure"])(
    "shows a %s without a silent retry",
    async (failure) => {
      const user = userEvent.setup();
      let contentReads = 0;
      const fetchMock = vi.fn<typeof fetch>((input) => {
        if (!requestUrl(input).endsWith("/content"))
          return Promise.resolve(artifactResponse());
        contentReads += 1;
        if (contentReads > 1)
          return Promise.resolve(
            new Response(html, { headers: { "Content-Type": "text/html" } }),
          );
        return failure === "unauthorized"
          ? Promise.resolve(new Response(null, { status: 401 }))
          : Promise.reject(new TypeError("Network failure"));
      });
      vi.stubGlobal("fetch", withSessionHistory(fetchMock));
      window.history.replaceState(null, "", "/?task=task_preview_12345678");
      render(<DesktopWorkspace />);

      expect(
        await screen.findByRole("button", { name: "Preview analysis.html" }),
      ).toBeVisible();
      await user.click(
        screen.getByRole("button", { name: "Preview analysis.html" }),
      );
      const dialog = screen.getByRole("dialog", {
        name: "Preview analysis.html",
      });
      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Preview could not be loaded.",
      );
      expect(contentReads).toBe(1);
      expect(
        within(dialog).queryByTitle("Artifact preview"),
      ).not.toBeInTheDocument();
      await user.click(
        within(dialog).getByRole("button", { name: "Retry preview" }),
      );
      expect(
        await within(dialog).findByTitle("Artifact preview"),
      ).toBeVisible();
      expect(contentReads).toBe(2);
    },
  );

  it("aborts loading on Escape and ignores content arriving after close", async () => {
    const user = userEvent.setup();
    const content = deferredResponse();
    const fetchMock = vi.fn<typeof fetch>((input) =>
      requestUrl(input).endsWith("/content")
        ? content.promise
        : Promise.resolve(artifactResponse()),
    );
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    window.history.replaceState(null, "", "/?task=task_preview_12345678");
    render(<DesktopWorkspace />);

    expect(
      await screen.findByRole("button", { name: "Preview analysis.html" }),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "Preview analysis.html" }),
    );
    const signal = fetchMock.mock.calls.find(([input]) =>
      requestUrl(input).endsWith("/content"),
    )?.[1]?.signal;
    expect(signal?.aborted).toBe(false);
    await user.keyboard("{Escape}");
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      content.resolve(new Response(html));
      await content.promise;
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Artifact preview")).not.toBeInTheDocument();
  });
});
