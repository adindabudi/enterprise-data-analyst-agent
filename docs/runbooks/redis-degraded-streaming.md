# Redis Degraded Streaming

Redis live streams are provisional. If Redis is unavailable, the task keeps running in the analyst runtime and the browser must use canonical task polling until streaming resumes.

Confirm Redis health through the configured health endpoint, then inspect the canonical task checkpoint before retrying dispatch. Do not reconstruct dropped message deltas from logs, prompts, or process memory.

After recovery, `stream.resumed` identifies the omitted sequence interval. The client may render retained milestones and polling state, but must treat missing provisional deltas as intentionally unavailable.
