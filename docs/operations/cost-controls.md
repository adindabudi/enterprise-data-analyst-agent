# Cost controls

## What you pay for

Several backing services run whether or not anyone is using the workspace. In the demo profile, the API, Hosted Agent compute, model tokens, and sandbox execution scale with usage.

| Service                        | Billed on                      | Demo profile              | Production profile                |
| ------------------------------ | ------------------------------ | ------------------------- | --------------------------------- |
| Container Apps (API)           | Replicas running               | 0 to 1, scale-to-zero     | 3 to 10 replicas                  |
| Foundry Hosted Agent           | Active hosted compute          | 5-minute idle timeout     | 5-minute idle timeout             |
| Container Apps Sandboxes       | Active sandbox compute/storage | Created per task, deleted | Created per task, deleted         |
| Redis Enterprise               | SKU, always on                 | `Balanced_B0`, no HA      | `Balanced_B10` with HA            |
| Cosmos DB                      | Autoscale RU/s plus storage    | 4,000 RU/s cap            | 4,000 RU/s cap, continuous backup |
| Foundry model                  | Input and output tokens        | Usage                     | Usage                             |
| Storage and Container Registry | Capacity and operations        | Usage                     | Usage                             |
| Log Analytics                  | Ingestion and retention        | 30 days                   | 90 days                           |
| Defender scanning              | Scanned volume                 | 100 GB cap                | 1,000 GB cap                      |

Both profiles cap sandbox concurrency at 10 and deny sandbox egress by default. There are zero ready sessions: each task creates an isolated sandbox and deletes it after completion or cancellation. Stopped sandbox snapshots can retain billable storage, which is why completed one-shot tasks delete rather than suspend their sandbox.

Hosted Agent compute is provisioned when a request arrives and deprovisioned after the configured 300-second idle timeout. Redis Enterprise and the other standing backing services continue to incur charges while the API and Hosted Agent are idle.

The default budget is 100 for demo and 1000 for production, in your billing currency. Provisioning refuses to start without `AZURE_MONTHLY_BUDGET_AMOUNT`.

This repository does not publish price estimates. Rates vary by region, currency, and agreement, and a stale number is worse than none. Put the table above into the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) for your region, then check it against the first week of real spend.

## Why you confirm three flags

The preflight will not provision until each of these is `true`. Each one commits you to a recurring charge or a posture that is awkward to reverse:

- `EDA_REDIS_SKU_CONFIRMED` — Redis Enterprise bills continuously, even at idle.
- `EDA_SANDBOXES_PREVIEW_CONFIRMED` — ACA Sandboxes are preview infrastructure and may require recreation after service changes.
- `EDA_DEFENDER_CONFIRMED` — Defender scanning bills per volume scanned.

## Budget alerts

Alerts fire at 50%, 75%, 90%, and 100% of actual spend, plus 90% of forecast, to the action group you configure. Set the budget to what you are willing to spend, not what you expect to spend.

## Cutting cost

Pull these in order. The first three cost you nothing that matters.

1. Keep the demo API minimum replicas at zero and the Hosted Agent idle timeout at 300 seconds.
2. Lower maximum sandbox concurrency if measured demand permits it.
3. Delete completed task sandboxes rather than retaining snapshots.
4. Shorten log retention.
5. Move Redis down a SKU, if your streaming load genuinely allows it.

Leave observability and recovery alone until those are exhausted. Losing telemetry costs more during one incident than it saves in a month.

Do not buy a reservation or savings plan before a pilot has produced a representative load report. Commitments on guessed demand are hard to unwind.

## Weekly review

Read token usage, sandbox minutes, and the API, Hosted Agent, Redis, Cosmos, storage, and telemetry lines together. A jump in one is usually explained by another: more analyses means more tokens, more sandbox time, and more Cosmos writes at once.
