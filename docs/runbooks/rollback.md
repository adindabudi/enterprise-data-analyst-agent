# Rollback

## ACA revision rollback

1. Stop promotion and identify the last healthy API and worker ACA revision.
2. Shift traffic to the healthy API revision and reactivate the matching worker revision.
3. Confirm readiness, a synthetic task, and the service objectives before reopening traffic.

## Model and image rollback

1. Revert the model deployment selection only to the previously approved model version.
2. Revert API, worker, and cleanup image references only to previously recorded immutable digests.
3. Do not roll forward mutable tags during an incident.

## Worker drain

1. Stop new task dispatch and allow in-flight work to reach a terminal state within the defined timeout.
2. Record opaque task IDs that require operational reconciliation.
3. Resume dispatch only after the replacement revision is healthy.

This topology has no regional HA claim. A regional failure requires a new, reviewed deployment rather than a failover assertion.
