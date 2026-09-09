import type { FabricAuthorizationStatus } from "../../api/fabric";

export type FabricAvailability = "disabled" | "failed" | "configured" | "ready";

export type FabricSourceContext = {
  availability: FabricAvailability;
  authorization?: FabricAuthorizationStatus;
};

export function fabricSourceName(context: FabricSourceContext): string {
  if (context.authorization?.source)
    return context.authorization.source.description;
  if (context.authorization?.provider === "ontology") return "Fabric ontology";
  if (context.authorization?.provider === "semantic_model")
    return "Fabric semantic model";
  return "Fabric source";
}

export function ontologySourceStatus(context: FabricSourceContext): string {
  if (context.availability === "disabled") return "Not configured";
  if (context.availability === "failed") return "Unavailable";
  if (context.authorization?.state === "reauth_required") {
    return "Reconnect required";
  }
  if (context.authorization?.state !== "linked") return "Sign in required";
  if (context.availability === "ready") return "Connected and ready";
  return context.authorization.chatQuery
    ? "Answers chat questions; deep analysis awaits acceptance"
    : "Signed in; not ready for queries";
}
