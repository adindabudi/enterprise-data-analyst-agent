# Analysis recovery

Use the opaque application task ID to inspect the canonical task record in Cosmos DB. Do not put tenant IDs, user IDs, prompts, cookies, or response bodies in incident notes.

Classify terminal state from the task record: `cancelled` is a completed business cancellation, while `failed` needs operational review. A closed browser does not cancel a task.

Every task runs in the API's analyst runtime. Its durable truth is the task record and the execution ledger (`capacity:analysis` in the runtime container), never a replica's memory:

- A task is stored with its `run_` attempt before it is queued, so a task one replica accepted but never ran is found again by any replica's recovery scan.
- Running a task needs a ledger claim. The claim is renewed while the task runs; a replica that loses it stops before it writes anything else.
- A nonterminal task with no live claim is recovered by the next scan. Whatever the previous owner left in the sandbox is deleted first, and an answer that was already published is checkpointed rather than produced twice.
- Queue age and the wall-clock budget are enforced by the supervisor, so a task cannot wait or run without bound.

A nonterminal task whose active attempt does not start with `run_` was owned by the retired Foundry hosted agent. Nothing can finish it any more, so reading it settles it as `failed`.

Cancel through the product API so the task record and the running job stay aligned. Never replay a completed external effect or publication by hand, and never reconstruct work from logs.
