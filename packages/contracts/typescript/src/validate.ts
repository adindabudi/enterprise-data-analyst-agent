import Ajv2020, { type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import activityEventSchema from "../../schema/activity-event.schema.json";
import type { ActivityEvent } from "./generated/activity-event";

const ajv = new Ajv2020({ allErrors: true, strict: true, discriminator: true });
addFormats(ajv);
const validateActivityEvent = ajv.compile<ActivityEvent>(activityEventSchema);

function describeErrors(errors: ErrorObject[] | null | undefined): string {
  return (errors ?? [])
    .map(
      (error) =>
        `${error.instancePath || "$"} ${error.message ?? "is invalid"}`,
    )
    .join("; ");
}

export function parseActivityEvent(value: unknown): ActivityEvent {
  if (!validateActivityEvent(value)) {
    throw new TypeError(
      `Invalid activity event: ${describeErrors(validateActivityEvent.errors)}`,
    );
  }
  return value;
}
