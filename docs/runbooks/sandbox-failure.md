# Sandbox failure runbook

Use this runbook for task-scoped ACA Sandboxes. Never enter a live sandbox shell, enable egress, expose a sandbox identifier, or attach an application identity to diagnose a failure.

## Allocation 429 or 5xx

Retry transient allocation failures with bounded backoff and the same idempotency record. Check Sandbox group quota, regional service health, and sanitized platform trace IDs. Do not route work to another group or image automatically.

## Sandbox not found

A 404 after explicit deletion is success. A 404 during active work means the sandbox expired or terminated; mark the operation failed and let the supervisor resume the task from canonical phase state. Never reconstruct an identifier from user data.

## Startup or health failure

Verify the immutable disk-image and application-image digests, nonroot UID/GID, and writable `/workspace/task`. Roll back to the last known digest if startup fails. Do not add debug endpoints or return package or host details from health output.

## Disk image registry authentication

For `401 RegistryAuthFailed`, first verify the Sandbox group identity, registry-scoped `AcrPull`, and ACR ARM-token authentication. Managed-identity disk-image import is still blocked in `azure-containerapps-sandbox==0.1.0b4`; Microsoft tracks the SDK issue in [azure-container-apps#1768](https://github.com/microsoft/azure-container-apps/issues/1768).

Until a fixed SDK is pinned and validated, `deploy-sandbox-group.sh` obtains a short-lived Entra-backed ACR token and streams it directly to the disk-image request over stdin. Never place this token in argv, an environment variable, a file, logs, or azd state. The token is only for the one-time OCI-to-disk import; task sandboxes remain credential-free and default-deny.

## Process timeout or pressure

Record only the execution ID, status, duration, bounded stderr artifact, and platform trace ID. Keep execution and memory ceilings intact; reduce task scope or document concurrency instead of exposing a generic shell.

## Output or archive limit

A file-count, byte, member, depth, path, symlink, or expansion failure is deterministic. Preserve the content-free validation report and mark the artifact incomplete/rejected. Do not relax limits, extract the archive manually in a live session, or publish an artifact that failed validation.

## Cleanup failure

One-shot work must delete its sandbox in `finally` after approved artifacts are persisted externally. Retry deletion with bounded backoff; the scheduled cleanup job reconciles expired task records but is not permission to retain a sandbox indefinitely. A retained stopped snapshot can still incur storage cost.

## Network or identity exposure

Any successful outbound network probe or runtime credential is a release blocker. Keep default-deny egress and the sandbox free of managed identities. Do not weaken either control to gather diagnostics.
