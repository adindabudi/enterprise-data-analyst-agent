import type {
  FabricAuthorizationStatus,
  FabricCapacityState,
} from "../../api/fabric";

export type FabricAvailability = "disabled" | "failed" | "configured" | "ready";

/** The capacity as the UI knows it; `checking` until the first answer arrives. */
export type FabricCapacityView = FabricCapacityState | "checking";

export type FabricSourceContext = {
  availability: FabricAvailability;
  authorization?: FabricAuthorizationStatus;
  capacity?: FabricCapacityView;
};

export const CAPACITY_PAUSED_DETAIL =
  "Capacity paused; queries can't run until it's resumed";

/** A linked source that answers queries, which is when its capacity matters. */
export function queryableSource(context: FabricSourceContext): boolean {
  return (
    context.authorization?.state === "linked" &&
    (context.availability === "ready" ||
      (context.availability === "configured" &&
        context.authorization.chatQuery))
  );
}

export function fabricSourceName(context: FabricSourceContext): string {
  if (context.authorization?.source)
    return context.authorization.source.description;
  if (context.authorization?.provider === "ontology") return "Fabric ontology";
  return "Fabric source";
}

export function ontologySourceStatus(context: FabricSourceContext): string {
  if (context.availability === "disabled") return "Not configured";
  if (context.availability === "failed") return "Unavailable";
  if (context.authorization?.state === "reauth_required") {
    return "Reconnect required";
  }
  if (context.authorization?.state !== "linked") return "Sign in required";
  if (queryableSource(context) && context.capacity === "paused") {
    return CAPACITY_PAUSED_DETAIL;
  }
  if (context.availability === "ready") return "Connected and ready";
  return context.authorization.chatQuery
    ? "Connected; acceptance pending"
    : "Signed in; not ready for queries";
}
