# Hosted Responses recovery

Use the opaque application task ID to inspect canonical Cosmos state and its active Hosted response attempt. Do not put tenant IDs, user IDs, prompts, cookies, or response bodies in incident notes.

Classify terminal state from the product task record: `cancelled` is a completed business cancellation, while `failed` requires operational review. A browser disconnect does not cancel a background response.

For a nonterminal task, compare its active attempt ID and checkpoint with the Foundry response status. Resilient Hosted Workflow checkpoints own execution recovery; Cosmos owns phase, idempotency, final-message, and artifact truth. Never replay a completed external effect or publication manually.

If the active attempt is irrecoverably terminal while the product task is nonterminal, persist the failure against that attempt before starting a replacement response from canonical phase state. Do not reuse an attempt ID or reconstruct work from logs.

Cancel through the product API so canonical state and the Foundry response stay aligned. Delete retained Foundry response data only after the owner-session retention policy permits it and canonical cleanup has completed.
