import { withSessionHistory } from "../test/session-history";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopWorkspace } from "./DesktopWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input);
}

function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const inputArtifact = {
  artifactId: "artifact-input_12345678",
  version: 1,
  kind: "input",
  sha256: "a".repeat(64),
  displayName: "uploaded-workbook.xlsx",
  sizeBytes: 2048,
};
const htmlArtifact = {
  ...inputArtifact,
  artifactId: "artifact-output_12345678",
  kind: "html",
  displayName: "analysis.html",
};
const docxArtifact = {
  ...inputArtifact,
  artifactId: "artifact-document_12345678",
  kind: "docx",
  displayName: "analysis.docx",
};
const queryArtifact = {
  ...inputArtifact,
  artifactId: "artifact-query_12345678",
  kind: "dataset",
  displayName: "query-rows.json",
};

describe("analysis details", () => {
  it("offers independently collapsible workspace sections without a tab strip", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", withSessionHistory(vi.fn()));
    render(<DesktopWorkspace />);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    for (const name of ["Tasks", "Inputs", "Outputs", "Provenance"]) {
      const toggle = screen.getByRole("button", {
        name: new RegExp(`^${name}`),
      });
      if (toggle.getAttribute("aria-expanded") === "true")
        await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("region", { name })).not.toBeInTheDocument();
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByRole("region", { name })).toBeVisible();
    }
  });

  it("distinguishes loading, failure, and an empty successful Outputs response", async () => {
    const user = userEvent.setup();
    const response = deferredResponse();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(response.promise)
      .mockResolvedValueOnce(jsonResponse({ artifacts: [] }));
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    window.history.replaceState(null, "", "/?task=task_details_12345678");
    render(<DesktopWorkspace />);

    expect(await screen.findByText("Loading outputs")).toBeVisible();
    expect(
      screen.queryByText("No ready artifacts yet."),
    ).not.toBeInTheDocument();
    await act(async () => {
      response.reject(new Error("Unavailable"));
      await response.promise.catch(() => undefined);
    });
    expect(screen.queryByText("Outputs could not be refreshed.")).toBeVisible();
    expect(
      screen.queryByText("No ready artifacts yet."),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Outputs" })).getByRole(
        "alert",
      ),
    ).toHaveTextContent("Outputs could not be refreshed.");
    await user.click(screen.getByRole("button", { name: "Refresh outputs" }));
    expect(await screen.findByText("No ready artifacts yet.")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("distinguishes provenance loading and errors from an empty query log", async () => {
    const user = userEvent.setup();
    const response = deferredResponse();
    let provenanceReads = 0;
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>((input) => {
          if (requestUrl(input).endsWith("/provenance")) {
            provenanceReads += 1;
            return provenanceReads === 1
              ? response.promise
              : Promise.resolve(
                  jsonResponse({ sourceQueries: [], artifacts: [] }),
                );
          }
          return Promise.resolve(jsonResponse({ artifacts: [] }));
        }),
      ),
    );
    window.history.replaceState(null, "", "/?task=task_details_12345678");
    render(<DesktopWorkspace />);
    await waitFor(() => {
      expect(window.location.search).toContain("session=");
    });
    await user.click(screen.getByRole("button", { name: "Provenance" }));
    expect(await screen.findByText("Loading provenance")).toBeVisible();
    expect(screen.queryByText("No queries yet.")).not.toBeInTheDocument();
    await act(async () => {
      response.reject(new Error("Unavailable"));
      await response.promise.catch(() => undefined);
    });
    expect(
      screen.queryByText("Provenance could not be refreshed."),
    ).toBeVisible();
    expect(screen.queryByText("No queries yet.")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Refresh provenance" }),
    );
    expect(await screen.findByText("No queries yet.")).toBeVisible();
    expect(provenanceReads).toBe(2);
  });

  it("downloads real canonical inputs and keeps only final kinds in Outputs", async () => {
    vi.stubGlobal(
      "fetch",
      withSessionHistory(
        vi.fn<typeof fetch>().mockImplementation(() =>
          Promise.resolve(
            jsonResponse({
              artifacts: [
                inputArtifact,
                htmlArtifact,
                docxArtifact,
                queryArtifact,
              ],
            }),
          ),
        ),
      ),
    );
    window.history.replaceState(null, "", "/?task=task_details_12345678");
    render(<DesktopWorkspace />);
    expect(await screen.findByText(inputArtifact.displayName)).toBeVisible();
    const inputs = screen.getByRole("region", { name: "Inputs" });
    expect(
      within(inputs).getByRole("link", { name: /^Download / }),
    ).toHaveAttribute(
      "href",
      "/api/tasks/task_details_12345678/artifacts/artifact-input_12345678/versions/1/content",
    );
    expect(
      within(inputs).getByRole("link", { name: /^Download / }),
    ).toHaveAttribute("download", inputArtifact.displayName);
    expect(
      within(inputs).queryByText(htmlArtifact.displayName),
    ).not.toBeInTheDocument();

    const outputs = screen.getByRole("region", { name: "Outputs" });
    expect(within(outputs).getByText(htmlArtifact.displayName)).toBeVisible();
    expect(within(outputs).getByText(docxArtifact.displayName)).toBeVisible();
    expect(
      within(outputs).queryByText(inputArtifact.displayName),
    ).not.toBeInTheDocument();
    expect(
      within(outputs).queryByText(queryArtifact.displayName),
    ).not.toBeInTheDocument();
    expect(
      within(outputs).getAllByRole("link", { name: /^Download / }),
    ).toHaveLength(2);
  });

  it("ignores a previous task's pending artifact response after new chat", async () => {
    const user = userEvent.setup();
    const response = deferredResponse();
    const fetchMock = vi.fn<typeof fetch>().mockReturnValue(response.promise);
    vi.stubGlobal("fetch", withSessionHistory(fetchMock));
    window.history.replaceState(null, "", "/?task=task_details_12345678");
    render(<DesktopWorkspace />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const newChat = screen.getAllByRole("button", { name: "New chat" })[0];
    if (!newChat) throw new Error("New chat control is unavailable");
    await user.click(newChat);
    await act(async () => {
      response.resolve(jsonResponse({ artifacts: [htmlArtifact] }));
      await response.promise;
    });

    expect(
      screen.queryByText(htmlArtifact.displayName),
    ).not.toBeInTheDocument();
    expect(screen.getByText("No ready artifacts yet.")).toBeVisible();
  });
});
