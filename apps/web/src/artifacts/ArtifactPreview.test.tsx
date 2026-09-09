import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ArtifactPreview } from "./ArtifactPreview";

describe("artifact preview", () => {
  it("does not render a preview before an artifact is ready", () => {
    render(
      <ArtifactPreview
        artifact={{ id: "artifact-1", status: "validating", kind: "html" }}
      />,
    );

    expect(
      screen.getByText("Preview unavailable until validation completes."),
    ).toBeVisible();
  });

  it("renders ready HTML inside a sandboxed iframe", () => {
    render(
      <ArtifactPreview
        artifact={{
          id: "artifact-1",
          status: "ready",
          kind: "html",
          content: "<h1>Ready</h1>",
        }}
      />,
    );

    const preview = screen.getByTitle("Artifact preview");
    expect(preview).toHaveAttribute("sandbox", "allow-scripts");
    expect(preview).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("connect-src 'none'"),
    );
    expect(preview).not.toHaveAttribute(
      "sandbox",
      expect.stringContaining("allow-same-origin"),
    );
  });
});
