# Service Level Objectives

These pilot objectives are measured from Azure Monitor and Application Insights over a rolling 30-day window. Alerting uses opaque task IDs only; it does not inspect request bodies or uploaded material.

| Objective                    | Target                   | Source                                  |
| ---------------------------- | ------------------------ | --------------------------------------- |
| API p95                      | `<500ms`                 | `requests`                              |
| First progress               | `<1s`                    | `eda.progress.first`                    |
| First model content          | `<10s`                   | `eda.model.first`                       |
| Cancellation acknowledgement | `<2s`                    | `eda.cancel.ack`                        |
| Sandbox termination          | `<15s`                   | `eda.session.stop`                      |
| Completion                   | `>=95%`                  | `eda.task.completed`, `eda.task.failed` |
| Structural publication       | `100%`                   | `eda.publish.structural`                |
| Pilot load                   | `20 active / 10 running` | Controlled load run                     |

Every `eda.*` name above is emitted by `apps/api/src/eda_api/metrics.py` or `services/worker/src/eda_worker/metrics.py`. Renaming one there disarms the query and the alert that reads it, without any other symptom.

## Queries

Application Insights stores these as pre-aggregated rows, one per export interval, so `count()` counts intervals rather than events and a true percentile is not recoverable from `customMetrics`. Read the latency objectives as a per-interval worst case and confirm a breach against `traces` before acting on it.

```kusto
// API p95
requests | summarize p95 = percentile(duration, 95) by bin(timestamp, 5m)

// First progress, cancellation acknowledgement, sandbox termination
customMetrics
| where name in ('eda.progress.first', 'eda.cancel.ack', 'eda.session.stop')
| summarize worstMs = max(valueMax), meanMs = sum(valueSum) / sum(valueCount) by name, bin(timestamp, 1h)

// First model content
customMetrics
| where name == 'eda.model.first'
| summarize worstMs = max(valueMax) by tostring(customDimensions.model), bin(timestamp, 1h)

// Completion
customMetrics
| where name in ('eda.task.completed', 'eda.task.failed')
| summarize completed = sumif(valueSum, name == 'eda.task.completed'), total = sum(valueSum)

// Structural publication: the instrument records 1 per passing validation and 0 otherwise
customMetrics
| where name == 'eda.publish.structural'
| summarize accepted = sum(valueSum), total = sum(valueCount)
```

Attributes on these instruments are kept to a handful of stable values on purpose. A metric stream that exceeds the SDK cardinality limit is folded into one overflow point carrying none of the original attributes, so adding a task or session ID would break every breakdown above, not only the one it was added for.

Review p95 and completion daily during a pilot. Split first model content by model before deciding whether an issue is an application regression or a model/provider condition.
