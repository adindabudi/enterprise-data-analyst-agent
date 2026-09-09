import { Text } from "@fluentui/react-components";

const WEB_ARTIFACT_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data: blob:",
  "font-src data:",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

export type PreviewArtifact = {
  id: string;
  status:
    | "created"
    | "generating"
    | "validating"
    | "repairing"
    | "ready"
    | "rejected"
    | "incomplete";
  kind: "html" | "image" | "spreadsheet" | "source";
  content?: string;
};

export function ArtifactPreview({ artifact }: { artifact: PreviewArtifact }) {
  if (artifact.status !== "ready") {
    return <Text>Preview unavailable until validation completes.</Text>;
  }
  if (artifact.kind === "html" && artifact.content) {
    return (
      <iframe
        title="Artifact preview"
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="${WEB_ARTIFACT_CSP}">${artifact.content}`}
      />
    );
  }
  return <Text>Preview metadata is ready for this artifact.</Text>;
}
