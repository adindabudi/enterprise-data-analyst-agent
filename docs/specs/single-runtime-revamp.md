# Single-runtime analyst revamp

Status: approved direction; implementation specification, not an implementation-complete claim.

Decision date: 2026-09-21. Repository baseline inspected: `f29da7d`.

## 1. Outcome

Replace the separate interactive/deep-analysis execution paths with one analyst runtime hosted alongside the API in one Azure Container Apps application. Use ACA Sandboxes only when code execution or artifact generation is needed.

Make analysis faster by removing repeated source discovery, duplicate queries, unnecessary model handoffs, and repeated data movement. Preserve the existing quality of insights, correct calculations, usable Excel workbooks, interactive dashboards, provenance, and deterministic publication gates.

The primary performance objective is at least 30% lower end-to-end p50 **and** p95 latency for each agreed workload class. This is a release target to demonstrate, not an estimated or already measured improvement. Because D09 fixes the model and reasoning profile, the artifact-producing class is the least certain to reach it; §12.3 defines the hard blockers that are never waived and the decision path for a class that misses the target.

This specification authorizes no deployment, production data migration, billable evaluation, or application code change by itself.

### 1.1 Functional requirements at a glance

Cite these IDs in pull requests, tests, and review comments. Each row points to the section that defines it and the check that proves it.

| ID   | Requirement                                                                                 | Defined in | Proven by                    |
| ---- | ------------------------------------------------------------------------------------------- | ---------- | ---------------------------- |
| FR01 | One engine serves every request; no deep-analysis mode and no separate analysis worker.     | §5, §6     | A01, A02                     |
| FR02 | Admission is durable and the queue is bounded, fair, and time-limited.                      | §6.1       | A26                          |
| FR03 | The analytical loop is evidence-driven and stops when the outcome is supported.             | §6.2       | A02, A07                     |
| FR04 | Existing required-output semantics are preserved without an added standalone planning pass. | §6.3       | A11, A12                     |
| FR05 | Source routing follows configured capability, never convenience.                            | §7.1       | A08                          |
| FR06 | Metadata discovery is lazy, scoped, reused, and correctly invalidated.                      | §7.2       | A01, A04, A05                |
| FR07 | Values, filters, units, and denominators stay semantically correct.                         | §7.3, §7.4 | A06, A07                     |
| FR08 | Queries stay inside documented engine limits.                                               | §7.4       | A28                          |
| FR09 | Identical in-task source work executes once.                                                | §7.5       | A03, A04                     |
| FR10 | Complete evidence is retained separately from bounded model context.                        | §7.6       | A09, A10                     |
| FR11 | Parallelism, retries, and timeouts are bounded; unknown outcomes are never zero rows.       | §7.7       | A27                          |
| FR12 | Insight quality, artifacts, and validation gates are preserved.                             | §8         | A11, A12, A13                |
| FR13 | Tasks survive disconnect and restart without repeating committed work.                      | §8.2, §9   | A14, A15, A17, A18, A23, A24 |
| FR14 | Ownership, capacity, and task budgets are enforced across replicas.                         | §9.3       | A16, A19, A25                |
| FR15 | Performance and quality gates are met on measured, comparable evidence.                     | §12        | §12.3, §12.4                 |
| FR16 | Migration preserves history and has defined rollback triggers.                              | §14        | A22                          |

## 2. Approved decisions

| ID  | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D01 | One API and agent application in ACA; one execution engine for all requests. No separate analysis-worker deployment and no user-facing deep-analysis mode.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| D02 | No Durable Task Scheduler (DTS), Dynamic Sessions, or Foundry Hosted Workflow for analysis execution in the target topology. Foundry model access remains.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| D03 | Allocate one ACA Sandbox lazily per task and reuse it within that task. No automatic sandbox reuse across tasks or users.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| D04 | Tasks survive browser disconnects and recover from application restarts using persisted checkpoints. Do not repeat successfully committed operations. Reconcile uncertain outcomes before retrying.                                                                                                                                                                                                                                                                                                                                                                                                             |
| D05 | Cosmos DB and Blob Storage remain canonical. Keep Redis for transient event delivery, not task ownership or recovery truth.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| D06 | Reuse identical query evidence within a task only when identity, authorization context, source, parameters, and freshness requirements match. New tasks obtain new source data by default.                                                                                                                                                                                                                                                                                                                                                                                                                      |
| D07 | Explore adaptively until the request and material insights have sufficient evidence. Additional queries must close a specific evidence gap; do not impose a universal three-query limit.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| D08 | Do not require new Fabric ontologies, semantic models, or data pipelines. Use existing configured sources and authoritative measures where available.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| D09 | Preserve the current model and reasoning profile in the initial optimization experiment. Do not claim a speed improvement obtained by weakening output quality or removing validators.                                                                                                                                                                                                                                                                                                                                                                                                                          |
| D10 | Target five concurrently executing tasks across conversations, with at most one executing task per conversation. Keep visible queuing, steering, and cancellation.                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| D11 | Keep at least one application replica running. Eligible interrupted tasks begin recovery within 60 seconds after the application and required dependencies are healthy.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| D12 | Preserve existing sessions, history, published artifacts, owner-scoped access, and optional-pack readiness requirements during migration.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| D13 | Fabric ontology sources follow the "snapshot + agent-written queries" pattern (decided 2026-10-01, measured in the Fabric tool-path benchmark of 2026-09-30). An operator-run job captures the schema once per ontology change; the runtime pins it in the agent's instructions and never discovers the source per question. The agent writes GQL for the graph and KQL for time series through the KQL database's Microsoft-hosted MCP server. `search_ontology` and runtime `list_ontology_entity_types` are not used. Queries keep running with the signed-in user's delegated token, not an agent identity. |

Approved product decisions are fixed for this revamp. Engineering defaults below are explicitly identified and may be tuned using evidence without changing these decisions.

## 3. Evidence and boundaries of the assessment

### 3.1 Observed implementation

All paths in this table refer to the inspected baseline, not proposed files.

| Observation                                                                                                                                                                                               | Source                                                                                                                                                                                         | Design consequence                                                                                                            |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| The repository already allocates ACA Sandboxes from a disk image with default-deny egress.                                                                                                                | [aca_client.py](../../services/worker/src/eda_worker/sandbox/aca_client.py), `create`, lines 113-130                                                                                           | Reuse and improve this adapter; do not introduce a replacement execution service.                                             |
| The UI has `deepAnalysis`; attaching a file selects that path.                                                                                                                                            | [Composer.tsx](../../apps/web/src/chat/Composer.tsx), lines 25-68                                                                                                                              | Remove mode selection, not upload or artifact functionality.                                                                  |
| Interactive chat can query data and then hand off to a separate task.                                                                                                                                     | [chat/service.py](../../apps/api/src/eda_api/chat/service.py), lines 32-41 and 562-582                                                                                                         | Remove the handoff and keep evidence inside one task from the beginning.                                                      |
| Deployment defines a separate `long-job` hosted agent.                                                                                                                                                    | [azure.yaml](../../azure.yaml), lines 25-76                                                                                                                                                    | Remove this analysis deployment after old work is safely drained.                                                             |
| Output requirements are extracted with a separate model request when needed.                                                                                                                              | [output_planning.py](../../services/worker/src/eda_worker/output_planning.py), `ensure`                                                                                                        | Keep the output contract while eliminating an unconditional separate planning pass.                                           |
| The main harness has a todo-driven loop, an eight-iteration outer limit, and a 64,000-token output option. The interactive API has different limits.                                                      | [factory.py](../../services/worker/src/eda_worker/agent/factory.py), lines 24-74; [main.py](../../apps/api/src/eda_api/main.py), lines 57-61                                                   | Consolidate limits deliberately. Do not accidentally apply the smaller chat ceiling to all artifact tasks.                    |
| API ontology access caches schema/tool validation, but opens and initializes an MCP session for each search. Worker ontology access initializes and lists tools for each verified session.                | [API ontology.py](../../apps/api/src/eda_api/fabric_auth/ontology.py), lines 76-150; [worker mcp_client.py](../../services/worker/src/eda_worker/fabric/ontology/mcp_client.py), lines 117-147 | Consolidate the two adapters and their different caching policies. It is incorrect to describe both as completely uncached.   |
| Worker grounding is cached per owner/session, task, ontology, and provider contract; explicit schema-purpose requests still inspect the source.                                                           | [ontology/gateway.py](../../services/worker/src/eda_worker/fabric/ontology/gateway.py), lines 71-92                                                                                            | Reuse valid schema-purpose results and define invalidation instead of repeatedly rediscovering.                               |
| Interactive graph calls execute before their result is added to the query ledger.                                                                                                                         | [chat/service.py](../../apps/api/src/eda_api/chat/service.py), lines 726-746                                                                                                                   | Add pre-execution exact-query reuse and in-flight coalescing. Recording a query is not deduplicating it.                      |
| Tool instructions encourage value discovery before filtering and separate ontology aggregates.                                                                                                            | [chat/service.py](../../apps/api/src/eda_api/chat/service.py), lines 43-89                                                                                                                     | Retain semantic safeguards but replace unconditional discovery and source-specific assumptions with evidence-aware decisions. |
| Graph output is reduced to fit an 8,000-character model response; truncation is reported. Ontology has separate 24,000-character limits.                                                                  | [graph.py](../../apps/api/src/eda_api/fabric_auth/graph.py), lines 186-216; [API ontology.py](../../apps/api/src/eda_api/fabric_auth/ontology.py), lines 16-21                                 | Separate complete analytical evidence from bounded model context.                                                             |
| `TaskRecord.query_results` has a ten-reference limit inherited from handoff/sandbox input constraints.                                                                                                    | [models.py](../../packages/runtime-state/src/eda_runtime_state/models.py), lines 73-94                                                                                                         | Do not use that bounded list as the complete evidence store for the unified task.                                             |
| Hosted Workflow checkpoints currently own execution recovery. Cosmos stores product state, but that is not automatically a replacement executor.                                                          | [hosted-responses-recovery.md](../runbooks/hosted-responses-recovery.md)                                                                                                                       | Implement and demonstrate application-owned recovery before removing Hosted execution.                                        |
| The `runtime` container defines no `defaultTtl`, while `RuntimeLocator` writes a per-item `ttl`. Cosmos ignores item TTL when container TTL is disabled [R9], so those locators most likely never expire. | [cosmos.bicep](../../infra/bicep/modules/cosmos.bicep), lines 107-121; [tasks.py](../../packages/runtime-state/src/eda_runtime_state/tasks.py), line 427                                       | Confirm this before reusing that container. Never make lease or slot correctness depend on Cosmos TTL deletion.               |

The repository already describes DTS as retired. Do not mistake deleting stale DTS names for removing the currently active Hosted analysis runtime.

### 3.2 What is not established

- No current end-to-end trace has established which component dominates the user's slow tasks.
- Durations mentioned in source comments are historical observations, not current p50/p95 measurements.
- The local sandbox vertical slice in [sandbox-baseline.md](../benchmarks/sandbox-baseline.md) is not a full agent/Fabric benchmark. That document also records missing current cloud acceptance evidence.
- No percentage saving, provider latency, cloud capacity, or regional availability is inferred from these code observations.
- Public guidance supports the query-shape recommendations below. It does not guarantee automatic Ontology MCP schema caching, application recovery, or equivalent semantics between every query route.

The review is workload-scoped: simplicity (RE:01), suitable execution services (PE:03), efficient code/data access (PE:07/PE:08), and measurable performance (PE:04/PE:06). It is not an assessment of the entire Azure estate.

## 4. Scope and non-goals

In scope:

- Unified task admission, agent execution, streaming, cancellation, steering, and recovery.
- One shared Fabric access layer for configured ontology and semantic-model providers.
- Query planning, metadata reuse, exact-result reuse, connection lifecycle, and complete evidence retention.
- Retaining and integrating the current sandbox, skills, artifact generation, validators, and publication rules.
- Updating affected frontend contracts, Python packages, Bicep, Terraform, build hooks, acceptance tooling, and runbooks.
- Measured baseline/candidate comparison and safe migration of the execution topology.

Out of scope:

- Replacing the model, lowering reasoning effort, or adding an automatic weaker-model fallback.
- Creating a new generic workflow engine, arbitrary DAG scheduler, message broker, or durable-functions service.
- Fabric source remodeling, new semantic models, ontology rebuilds, capacity purchases, or cross-source federation as prerequisites.
- Removing Redis, Cosmos, Blob, authentication, provenance, upload scanning, or artifact validation to reduce component count.
- Enabling previously disabled optional packs or reviving the retired Power BI Project Pack.
- Changing geographic/data residency choices or widening data access to make an optimization work.

## 5. Target topology and responsibility boundaries

| Component              | Responsibility                                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web UI                 | One composer and task experience; progress, source evidence, steering/cancel, artifacts, and reconnect.                                                             |
| ACA application        | FastAPI, authentication, task admission, a single analyst runtime, bounded in-process task supervision, and the existing owner-scoped APIs.                         |
| Analyst library        | Model loop, evidence requirements, Fabric tools, sandbox tools, quality gates, and finalization. A library, not a separately deployed service.                      |
| ACA Sandbox            | Untrusted generated Python/JavaScript, local calculations, reviewed artifact tooling, and deterministic validation execution. No application or Fabric credentials. |
| Cosmos DB              | Messages, task state, commands, operation records, leases, evidence metadata, readiness, and publication metadata.                                                  |
| Blob Storage           | Complete query-result bytes, approved inputs, checkpoint payloads that are too large for task records, and published artifact bytes.                                |
| Redis                  | Disposable live-event delivery and best-effort wakeups. Losing it must not lose task truth.                                                                         |
| Foundry model endpoint | The existing approved model/reasoning profile. No Hosted analysis agent is needed in the final topology.                                                            |
| Fabric                 | Existing configured read-only data sources, called with the authorized user's security context.                                                                     |

All requests enter the same engine. A lookup may use one source query and no sandbox. An Excel request may use queries, computation, validation, and publication. These are different amounts of work, not different runtimes or a hidden fast/deep router.

Keep API request handling responsive: SDK calls and CPU-heavy work must not block the ASGI event loop. Heavy generated computation remains in the sandbox. Clients and connection pools are initialized through application lifespan and closed cleanly.

## 6. Unified request and agent execution

### 6.1 Admission

1. Authenticate the caller and check session ownership and CSRF protection.
2. Persist the user message using the existing idempotency semantics.
3. Create or return the canonical task for that message and idempotency key.
4. Record queue eligibility durably before returning HTTP 202. A notification to an in-memory supervisor or Redis is only a latency optimization.
5. Select queued work subject to the deployment-wide five-task limit and the per-conversation reservation.
6. Load the task's canonical context and resume position; do not depend on the browser's history or an in-memory agent object.

New submissions in an already executing conversation remain visibly queued. They are not silently interpreted as steering. Explicit steering and cancellation target the existing task.

Bound the queue with configured limits rather than intentions. Reject a submission beyond the depth with a clear retry-later response instead of accepting work the deployment cannot start, and fail a task that exceeds the queued age with a truthful reason rather than leaving it pending forever. Select queued work in fair order across owners and conversations, not purely first-come, so one owner cannot occupy the whole queue.

| Limit                        | Engineering default                                              | Behavior at the limit                                                                     |
| ---------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Deployment queue depth       | 50 waiting tasks                                                 | Reject further submissions with a retry-later response that states the condition.         |
| Per-owner share of the queue | 20% of depth, so 10 waiting tasks                                | Reject that owner's further submissions while other owners continue to be admitted.       |
| Maximum queued age           | 15 minutes                                                       | Fail the task with an explicit queue-timeout reason; never leave it pending indefinitely. |
| Selection order              | Fair across owners, then across conversations, then oldest first | One busy owner cannot starve others.                                                      |

These defaults are configurable and must be reported, not hard-coded constants discovered later in source. Report queue depth, queued age, per-owner occupancy, and rejection counts; queue wait is part of the measured end-to-end latency in §12.

An authorization-blocked task or a task waiting for a material user clarification retains its position in the conversation but releases its execution slot. Later ordinary tasks in that conversation do not overtake it; the user can supply the required input or cancel it. Persist the response as an owner-scoped command and resume the same task. Tasks in other conversations continue.

### 6.2 One analytical loop

The runtime maintains a bounded evidence checklist: requested outcomes, relevant metrics/entities, grain, filters, time range, units, known evidence, and remaining gaps.

- Reuse the current analytical instructions that produce material comparisons, anomalies, implications, and supported next actions.
- Keep a narrow lookup narrow. Do not expand every request into a full business review.
- Each additional source call records a short purpose such as `resolve_value`, `answer_metric`, `test_anomaly`, `reconcile_total`, or `fetch_detail`.
- Once all requested outcomes and material findings are supported, finalize. Do not query just to fill a plan or keep a loop running.
- A quality gate may require more work. A budget exhausted before the outcome is supported produces an explicit incomplete/blocked result, never a success-shaped answer.
- Persist concise plans, tool inputs/results, and completion state, not hidden model reasoning.

Do not introduce a separate model router, a second summarizing agent, or mandatory source-query handoff. Deterministic validation and publication should be application-controlled once their prerequisites are satisfied, rather than requiring redundant LLM turns for each mechanical step.

### 6.3 Required outputs

Retain the current `RequiredOutput`/`OutputContract` semantics:

- Only requested downloadable formats are required; an XLSX export does not automatically require an HTML dashboard.
- Uploaded input formats are not inferred to be requested output formats.
- Preserve counts, explicit exclusions, unsupported-format errors, and ordered steering changes.
- A repair attempt cannot weaken or remove output requirements.

The default path is that the primary agent emits a validated structured output contract during its first ordinary planning step, so no separate standalone planning model pass is added. A deterministic shortcut that skips the model entirely is permitted only for a request whose requested formats are explicit, and only if that shortcut passes the complete existing output-contract test set unchanged, including format synonyms, counts, explicit exclusions, uploaded-input exclusion, and unsupported-format reporting. If it cannot pass all of them, keep the model-authored contract. A brittle keyword parser that quietly narrows today's semantics is a quality regression, not an optimization.

This consolidation must pass the existing output-contract cases before replacing the current planner. A malformed contract is corrected within the same bounded runtime; a material ambiguity is clarified with the user.

## 7. Fabric query-efficiency contract

### 7.1 Shared, source-aware routing

Expose one application-owned source catalog with capabilities, business descriptions, supported query routes, schema identity, and readiness. Do not expose internal endpoints or credentials in model prompts.

| Requirement                                                                                                         | Preferred existing capability                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Entity relationships, dependency chains, or graph-backed property aggregates                                        | Direct read-only GQL when the configured graph exposes the necessary data and semantics.                                                                                                      |
| A governed KPI, measure, hierarchy, or time-intelligence calculation already defined in a configured semantic model | The existing semantic-model/DAX capability; do not approximate its business logic with a graph count.                                                                                         |
| Ontology-bound time series, which the graph does not hold                                                           | Read-only KQL, written by the agent, through the bound KQL database's Microsoft-hosted remote MCP server (`executeQuery`), per D13. The natural-language `search_ontology` route is not used. |
| A calculation or visualization over evidence already obtained                                                       | Local deterministic computation; use the sandbox when computation/code is required. No new Fabric query solely to reformat the same values.                                                   |

This is capability routing, not permission to query a new source. If no configured authorized route supports the request, report that limitation. Resolve ambiguous sources or business meanings rather than silently selecting one.

GQL means Graph Query Language, not GraphQL. Do not substitute Fabric GraphQL MCP documentation or Cypher syntax for the configured GQL endpoint.

Do not encode historical failures of one natural-language query as universal Fabric limitations. Retain source-specific regression cases and document supported behavior from the actual provider contract.

### 7.2 Metadata discovery and MCP sessions

For an ontology source, D13 replaces run-time discovery with a pinned schema snapshot:

- An operator-run job (`scripts/build-fabric-schema-snapshot.py`) reads `list_ontology_entity_types` once, adds relationship names and directions with one GQL introspection query, and adds the stored values of each low-cardinality string property with one grouped GQL query each. The listing alone lacks both: in the benchmark, a snapshot without stored values answered 10/12 because the agent filtered on a value the data does not hold, and 12/12 with them.
- The job runs when the ontology changes, never per question. It publishes the snapshot to the runtime container; the API loads it at startup and fails closed, registering no source tools, when it is missing, invalid, or bound to another source.
- The runtime renders the snapshot into the agent's instructions for a user whose Fabric link is confirmed, and withholds it otherwise. It is deployment configuration: every linked user of the deployment sees the structure and listed values, while every query still runs with that user's token.
- Measured with one model over the same questions, per-question discovery took 20.2 s, 4.4 model turns and 27k input tokens; the snapshot took 10.1 s, 2.2 turns and under 5k tokens.

The rules below still govern what remains discovered or held at run time (graph identity when `graphModelId` is not configured, the KQL tool contract, and MCP sessions):

- Discover metadata lazily when a task actually needs that source. A non-Fabric conversation must not pay for schema and relationship discovery.
- Reuse schema, relationships, and compatible tool contracts across tasks for the same principal and source, subject to the scope and revalidation rules below.
- Discover relationship names only when needed for a traversal. Prefer supported metadata APIs already accessible with current permissions. Do not request wider permissions simply to avoid a query.
- Where relationship discovery needs a data query, make it targeted and reuse its result. A sampled/incomplete relationship inventory must not be represented as the entire schema.
- Use single-flight initialization so parallel requests do not all perform the same cold discovery.
- Reuse authenticated MCP sessions/HTTP connections where the SDK and server support it. Scope them to endpoint and authorization context; close/reconnect on expiry, logout, revocation, incompatible contract change, or transport failure.
- Revalidate tool compatibility on a new transport session or a reported tool-list change. No `list_tools` before every query on an already validated unchanged session.
- Keep `naturalLanguageResponse=false` for ontology calls when raw structured evidence is sufficient; the primary agent owns the final explanation.
- Treat every ontology and semantic-model call as synchronous. Only the Fabric data-agent endpoint documents background mode; ontology and semantic-model endpoints run synchronously [R2], so no analysis step may depend on a long-running ontology call. The "standard tool-call timeout" named in that documentation describes the Foundry-hosted tool path. This deployment calls the ontology MCP endpoint directly from the analyst runtime, so the application owns that deadline and must set it explicitly rather than inherit an assumed one.

#### Two caches with different lifetimes

Structure and business values are cached under different rules. Conflating them either leaks stale values or forces every task to pay cold discovery, and this specification rejects both.

|             | Source metadata cache                                                                                            | Query evidence cache                                                           |
| ----------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Holds       | Entity and property names, types, relationships, units, validated tool contract                                  | Returned business values and aggregates                                        |
| Key         | Product tenant, source tenant, authorized principal, source item, provider contract, and schema/version identity | The §7.5 fingerprint, which additionally includes the task and freshness epoch |
| Lifetime    | Survives across tasks for the same principal and source while still valid                                        | Task-scoped, per D06                                                           |
| Governed by | This section                                                                                                     | D06 and §7.5                                                                   |

D06 governs business values. It does not require a new task to rediscover a schema that has not changed. A second task from the same user against the same unchanged source starts warm; that is a deliberate part of the latency objective, not a violation of evidence scoping.

Metadata is still owner-scoped. Entity and property names describe a customer's business, so a process-global cache keyed only by URL is not acceptable. Never reuse a metadata entry across principals, and never let a cached entry stand in for a permission check; §11 still requires current grant state before protected reuse.

Engineering default: revalidate a metadata entry at most every five minutes when no reliable source version or change notification is available, and refresh immediately on schema or permission errors, configuration changes, explicit refresh, or an incompatible provider response. This is an application policy, not a Fabric guarantee. Persist the digest and observation time so recovery does not rediscover; never pretend a locally computed digest detects an unseen remote edit.

Valid requested schema output must come from the same governed cache policy as internal grounding. Do not bypass the cache merely because the tool's purpose is `schema`.

### 7.3 Stored values and semantic correctness

Do not blindly run a distinct-values query before every filter.

Use an already verified property definition, curated value mapping, or same-task domain evidence when it resolves the request. If the mapping is genuinely unknown, use one targeted discovery/validation query and retain that evidence. A user's requested label identifies intent; it does not prove that a stored value or similarly named field exists.

Ambiguous attributes, missing values, units, entity keys, and denominator definitions remain correctness checks. Do not remove them to meet a query-count target. A bounded top-values preview is not proof that an unreturned value is absent.

### 7.4 Query shapes

Apply Fabric's documented GQL guidance [R3]:

- Push filters into supported pattern-level predicates.
- Project only needed properties; avoid full-node output when a narrow projection is sufficient.
- Combine related traversals when they answer the same evidence requirement without changing grain or population.
- Avoid N+1 per-entity calls. Fetch the required related set or grouped results once.
- Use explicit relationship directions, bounded hop ranges, and key filters where appropriate.
- Use shared variables to prevent accidental Cartesian products.
- Use `TRAIL` only where its edge-uniqueness semantics match the question, not as a blind rewrite.
- Aggregate at the source where the route can express the intended calculation correctly.

Respect these documented graph limits [R3][R8]. Treat them as engine boundaries, not as targets to approach:

| Documented limit        | Value                                                                                                                                                                                         | Design consequence                                                                                                                                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Variable-length hops    | Up to 8                                                                                                                                                                                       | Reject a generated traversal that exceeds it. Use the tightest range that answers the question.                                                                                                                                      |
| Query response size     | Responses **larger than** 64 MB are truncated                                                                                                                                                 | A truncated response is `sourceResultIncomplete`. A response merely near the threshold is not evidence of truncation: mark it `unknown` and resolve it with a control total or a bounded partition before any full-population claim. |
| Aggregation stability   | Unstable when results exceed 128 MB                                                                                                                                                           | Filter and group before aggregating; do not aggregate an unbounded match.                                                                                                                                                            |
| Query timeout           | 20 minutes                                                                                                                                                                                    | Bound query cost deliberately. A timeout is an unknown outcome, not an empty result.                                                                                                                                                 |
| Export and continuation | The GQL Query API documents continuation polling for a query that is still running (`02000` with `result.nextPage`), but no continuation for rows cut by truncation and no server-side export | Follow `nextPage` within the tool's own deadline and never read an unfinished `02000` as zero rows. Completeness for a large export comes from validated disjoint partitions and control totals, never an invented cursor.           |

Recheck these values before implementation; the graph limitations page and the performance guidance page have differed on graph-size figures, so cite the limitation actually verified at the time.

Combining queries must not change denominators or multiply measures through one-to-many joins. Compare base-population totals and filtered subsets at the correct grain. Test zero matches, missing relationships, nulls, duplicate edges, and distinct-entity counts.

Use `LIMIT` for previews, bounded detail, and a genuinely requested top-N. Never apply a sample limit before a population aggregate and then present it as the full total. The configured graph endpoint documents continuation only for a query that is still running, not for truncated rows, and no server-side result export [R8], so completeness for a large export comes from validated disjoint partitions reconciled against a control total. Do not invent continuation support for rows the endpoint has cut.

### 7.5 Exact-result reuse and duplicate suppression

Before dispatch, compute a fingerprint from:

- Task and freshness epoch.
- Product tenant, source tenant, owner/principal, and authorization-context generation.
- Provider/source identity and schema/provider-contract identity.
- Exact structured query/parameters, projection, filters, grain, units, and resolved time boundaries.
- Query route and provider options affecting result semantics.

An invocation ID identifies one attempt; it must not be the only key for semantic duplicate detection.

For a completed, compatible fingerprint, return the persisted evidence reference and useful bounded summary without a network query. For an identical in-flight fingerprint, await the same operation instead of launching a second one.

Do not use fuzzy prompt similarity as an exact-query cache. Do not normalize string literals, identifiers, time zones, or parameters in a way that changes meaning. If safe canonicalization is not available, prefer exact structured inputs over speculative equivalence.

Do not cache errors as successful empty results. An explicit freshness change creates a new evidence epoch. A new task does not silently inherit old business values. Historical artifacts may be reused only when intentionally selected as historical inputs and labeled accordingly.

Relative time expressions must be resolved against a recorded task time where supported. If a provider query uses a moving clock or other nondeterministic behavior that cannot be bound safely, exclude it from exact-result reuse or clearly preserve it as an as-of observation.

### 7.6 Complete evidence, bounded model context

Replace character-truncated analytical results with two separate products:

1. An immutable owner-scoped Blob payload containing the complete provider result obtained, with hash, query identity, timestamp, completeness information, and provenance metadata.
2. A bounded model-facing response containing relevant aggregate values, selected rows, row counts when known, a schema/units summary, and the full-evidence reference.

Distinguish `contextPreviewTruncated` from `sourceResultIncomplete`. The former can be safe; the latter must prevent full-population claims or complete-export claims until resolved.

Represent completeness as verified, incomplete, or unknown. Do not infer a complete population or its total row count from the size of a returned sample. When the provider gives no reliable completeness signal, use a supported control-total/partition strategy or surface the uncertainty.

If the upstream engine itself truncates or times out, storing all returned bytes does not make the result complete. Narrow or partition the query without changing its meaning, or report the limitation.

Do not serialize the full evidence collection into the current ten-entry `TaskRecord.query_results` field. Store query operations/evidence in owner-scoped records and pass a verified input manifest to the sandbox. Preserve existing upload size/count limits unless separately justified; removing a handoff limit does not authorize unbounded data transfer.

The model should receive small usable query results directly rather than a generic success message that forces an extra inspect-tool round trip. Large results remain accessible through their evidence references.

### 7.7 Parallelism and retries

Parallelize only independent read operations after their source, schema, security, and semantic dependencies are resolved. Do not parallelize a value-discovery operation with a query whose filter depends on that result.

Use bounded per-task and per-source concurrency, shared backpressure, connection limits, and provider retry guidance. Engineering starting point: at most two independent source reads per task, subject to a shared source limit and measurement. Five active tasks must not become unbounded provider fan-out.

Do not blindly repeat parse errors or the same failed query shape. Repair with the returned diagnostic and current schema. Retry transient failures with bounded backoff and cancellation checks; honor provider throttling signals.

Every source call carries an explicit application-owned deadline. Because the direct MCP path inherits no documented tool-call timeout, define the deadline in configuration and keep it below the engine limit the query itself faces, such as the documented 20-minute graph timeout [R8]. Size an expensive query against that limit rather than waiting for the engine to decide. A timed-out call is an unknown outcome: apply the §9.4 uncertain-operation rule, and never record it as zero rows. When a question cannot fit inside a synchronous call, split it into bounded partitions rather than extending the deadline indefinitely.

For natural-language ontology calls, combine questions only where the provider's returned structure demonstrably preserves all requested measures and populations. Retain separate calls when combining would lose accuracy.

## 8. Computation, insights, and artifacts

### 8.1 Preserve the useful analytical behavior

The final answer must still:

- Answer every requested part, with filters, units, population/grain, and time context.
- Identify relevant material patterns, comparisons, anomalies, business implications, and supported next actions for analysis requests.
- Distinguish observed values, computed results, and hypotheses.
- Avoid causal claims not supported by the available evidence.
- Reconcile important totals and link important claims to query evidence or validated artifacts.

Do not trade away this behavior by merely instructing the agent to "use fewer tools" or "answer faster."

### 8.2 Sandbox lifecycle

- Allocate only on the first required code/artifact operation, using the existing reviewed image and default-deny egress.
- Reuse that sandbox for the task's subsequent operations; do not allocate one per tool call or file.
- Keep approved dependencies preinstalled. No runtime package installation as a routine workflow.
- Deduplicate input transfers by content hash and sandbox/task identity.
- Persist the sandbox identity and file manifest. The adapter's current in-memory `_files` index must not be required to recover a task.
- Use operation-specific working/output locations and committed manifests. A failed/retried command must not overwrite a previously committed output in place.
- Keep credentials, broad managed identities, and direct Fabric access out of generated-code execution.
- Preserve idle suspend as a platform optimization, not the source of recovery truth. Do not suspend active work accidentally during a long command.
- After terminal completion and durable publication, dispose of scratch compute using bounded lifecycle/cleanup controls. Maintain a recovery grace period for interrupted tasks.

A task must recover from a deleted sandbox by rebuilding it from the image, approved inputs, and committed outputs. Memory/disk snapshots are useful optimizations but not substitutes for application checkpoints [R1].

#### What the pinned SDK actually provides

Verify this against the SDK version in the lockfile before implementing. At the inspected baseline, `azure-containerapps-sandbox==0.1.0b4` exposes:

| Available                                                                      | Not available                              |
| ------------------------------------------------------------------------------ | ------------------------------------------ |
| `exec(command, working_directory=...)` returning exit code, stdout, and stderr | A server-assigned execution ID             |
| `write_file`, `read_file`, `list_files`, `delete_file`, `mkdir`                | A lookup API for a command issued earlier  |
| `stop`, `resume`, `wait_for_running`, `ensure_running`, `delete`               | A cancel or kill API for a running command |
| Snapshots, egress policy, labels, volumes                                      | A reattach path after a dropped connection |

The current adapter also assigns its execution ID _after_ `exec` returns, so a dropped connection loses the identity of the work it started. A design that assumes the control plane can be asked "did command X finish?" is not implementable on this SDK.

The recovery record must therefore live in the sandbox filesystem, which both sides can read.

#### Execution identity and receipts

- Generate the execution ID in the application **before** dispatch and derive it from the task, operation, and attempt so a replay produces the same ID.
- Write the execution intent into the sandbox before starting work, so the sandbox knows its own execution ID.
- Launch work detached inside the sandbox rather than relying on the synchronous call to stay open. The call returns once the work has started; the application then polls for the receipt. A dropped connection then loses a poll, not the work.
- The in-sandbox runner writes a start marker, refreshes a heartbeat while running, and writes a single completion receipt at the end containing exit code, output manifest, and content hashes. Write the receipt atomically, for example to a temporary name followed by a rename, so a partial file is never read as a result.
- Commit the durable operation record from the receipt, under the §9.3 fencing generation.

#### Determining status after an interruption

Read the sandbox filesystem and classify. Never infer status from the application's own timeout.

| Evidence                                  | Status      | Required behavior                                                                                                        |
| ----------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------ |
| No start marker                           | Not started | Safe to dispatch under the same execution ID.                                                                            |
| Start marker, fresh heartbeat             | Running     | Keep polling. Do not start a second copy.                                                                                |
| Start marker, stale heartbeat, no receipt | Unknown     | Treat as §9.4 uncertain. Do not claim zero rows, an empty output, or a failure. Terminate deliberately before any retry. |
| Receipt present                           | Completed   | Reuse it. Never re-run a completed execution.                                                                            |
| Sandbox absent                            | Lost        | Rebuild from image, approved inputs, and committed outputs; replay only what has no receipt.                             |

A stale heartbeat means the application cannot see progress. It does not prove the process stopped.

#### Stopping work, and what that costs

There is no command-level cancel, so stopping is deliberate and has exactly two forms:

1. **Cooperative.** The runner checks a cancel flag between steps and exits. Preferred, bounded, and it preserves the sandbox and its outputs.
2. **Forceful.** Delete the sandbox. This is the only guaranteed stop, and it destroys uncommitted scratch state. Committed outputs already published are unaffected.

`stop` is a suspend that preserves state and resumes later [R1]. It is never a cancellation. Do not use it to end work.

This resolves the apparent conflict between reconciling before reuse and releasing capacity on a watchdog. The capacity slot is released when the execution is **known stopped**, which means a receipt exists, cooperative cancellation confirmed exit, or sandbox deletion was confirmed. On watchdog expiry: request cooperative cancellation, wait a bounded interval, then delete the sandbox and confirm deletion, and only then release the slot. Capacity is never released on a timer alone.

### 8.3 Artifact pipeline

Retain current Excel, chart, dashboard, and enabled optional-document capabilities.

- Keep formula/control-total checks, file integrity checks, and provenance bindings.
- Preserve the reviewed web-artifact skill, offline bundling, runtime-startup checks, and the required `web_artifact_html` validation profile.
- Preserve requested desktop/mobile visual acceptance where currently required.
- Run deterministic validation against the exact bytes/version to be published. A changed file invalidates an old passing report.
- A model-authored claim that validation passed is not a validation result.
- Batch independent checks when safe; do not skip checks or erase diagnostic reports.
- Repair only failed outputs from existing evidence where possible. Do not rerun Fabric queries because HTML layout failed.
- Publish validated files through the existing owner-scoped storage/download boundary; do not serve generated HTML as trusted application-origin code.

The application can automate validation and idempotent publication after generation. A task is complete only when required files are durably published and the final answer is durably stored.

## 9. Durability without a separate worker

### 9.1 What replaces Hosted execution

An in-process supervisor in the ACA application owns task lifecycles. It is not a separately deployed worker and is not attached to the lifetime of an HTTP request.

Its scope is intentionally small: admit bounded tasks, acquire/renew ownership, resume persisted steps, process controls, and finalize. Do not add a generic workflow DSL, arbitrary scheduled DAGs, or a second execution service.

Use a supervised task group with explicit exception reporting and graceful shutdown, not untracked fire-and-forget `asyncio.create_task` calls or FastAPI request background tasks as a durability guarantee.

Keep at least one replica running. Short submission requests and reconnectable SSE must not be required to remain open for the lifetime of an analysis. HTTP-only scaling is not a reliable signal for work continuing after the client disconnects.

### 9.2 Durable records

Extend/reuse the existing runtime-state package rather than inventing a parallel task store.

| Record                   | Required meaning                                                                                                                                                                                                                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Task                     | Owner/session, schema version, engine version, queue state, current phase, checkpoint sequence, owning instance, fencing generation, ownership lease expiry, active attempt, cancellation state, required outputs, and final-message reference. The fencing generation here is the authority every durable write checks. |
| Capacity ledger          | One document holding at most five active entries. Each entry names a task, its owning instance, and an expiry. It grants permission to start, never permission to commit.                                                                                                                                                |
| Conversation reservation | The current eligible/active task and ordering for queued requests in that conversation.                                                                                                                                                                                                                                  |
| Operation                | Stable semantic input hash, status, attempt identity, provider/command identity if available, immutable result reference, and timestamps.                                                                                                                                                                                |
| Evidence                 | Query fingerprint, source/auth/schema identities, as-of time, completeness, immutable bytes hash/reference, and derivation/provenance.                                                                                                                                                                                   |
| Sandbox manifest         | Task/sandbox/image identity and committed source, input, output, and validation file mappings.                                                                                                                                                                                                                           |
| Command                  | Ordered idempotent steering/cancel instructions and the last applied sequence.                                                                                                                                                                                                                                           |

Add `queued` and `waiting_for_input` states and explicit recoverable attempt metadata without collapsing existing execution phases into an ambiguous generic success/failure field. Preserve existing terminal outcomes, including cancellation failure.

Persist resumable agent/tool context after completed tool batches. Do not store large raw result sets in the task document. The restart point is a committed tool/model boundary, not an in-memory stack frame.

#### Agent Framework persistence contract

The repository already contains the right primitives. Extend them; do not write a parallel agent-state store.

| Existing component                                                                       | Role to keep                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`ProjectionHistoryProvider`](../../services/worker/src/eda_worker/history/provider.py)  | A `HistoryProvider` that persists messages and the serialized session to Cosmos with ETag conflict handling, assigns stable message IDs, and strips reasoning and protected content before storage. |
| [`SessionHydratingAgent`](../../services/worker/src/eda_worker/agent/session_adapter.py) | Restores the session for a trusted task/partition, enqueues the source message and ordered pending commands, and rejects caller-supplied model options.                                             |
| [`create_primary_harness`](../../services/worker/src/eda_worker/agent/factory.py)        | Composes the harness, tools, context providers, compaction, and bounded looping.                                                                                                                    |

Required behavior in the unified runtime:

- **Use the harness rather than a hand-written loop.** The harness already drives function invocation and persists history after each model call inside a tool-calling run [R10]. Reimplementing that loop is the thing §4 rules out.
- **Pass the durable history provider explicitly.** `create_harness_agent` defaults to in-memory history [R10], which loses a task on restart. The configured provider must be the Cosmos-backed one.
- **Persist the whole session, not just message text.** `AgentSession` is an opaque state object carrying `session_id`, an optional `service_session_id`, and a mutable `state` dictionary that context providers write into, including todos, mode, and approvals. Serialize and restore it as a unit [R11].
- **Bind every stored session to its owner.** Resume only after verifying the authenticated tenant and owner, and never accept a session or service-side conversation identifier supplied by a client [R11]. The current projection already enforces partition ownership and rejects a session belonging to another analysis; keep that.
- **Keep history local to the application.** The existing projection refuses a stored `service_session_id`, so conversation state stays in Cosmos rather than in a provider-side conversation. Preserve that unless a separate decision changes it, because a provider-managed conversation would move part of the recovery story outside the canonical store.

#### Session identity and compatibility

Agent Framework states plainly that a session must be restored with the agent and provider configuration that created it, and that reusing one across a different configuration can produce invalid context [R11].

Record alongside each stored session the engine version, model profile and served snapshot, prompt hash, and tool-contract hash. On restore, compare them.

| Comparison                                            | Required behavior                                                                                                                   |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Identical                                             | Restore and continue normally.                                                                                                      |
| Compatible additive change                            | Restore, record the difference, and keep the task's original contract for output requirements.                                      |
| Incompatible change to model, prompt, or tool surface | Do not silently restore. Finish the task truthfully as interrupted, or start a new attempt with a fresh session lineage and say so. |

This is the same rule as §14: an old task keeps its `engineVersion`, and a new runtime must not adopt a session it cannot faithfully continue.

#### History persistence is not idempotency

Per-service-call persistence bounds how much model work is repeated. It does not tell you whether a tool's external effect committed.

- History may record that a tool was invoked while the operation record shows no commit, because the process died between the call and the commit.
- Authority over "did this really happen" belongs to the §9.2 operation and evidence records and their §9.3 fencing generation, never to the message history.
- On resume, reconcile in that order: operation record first, then history. A tool call present in history with no committed operation is an §9.4 uncertain operation.
- Wrap tool dispatch in function middleware so the operation record is opened before the effect and closed from its result, on the same layer the harness already invokes per tool call [R12].
- Reasoning content is deliberately excluded from the projection. Resume restores decisions and results, not the model's internal chain. Treat that as intended, and do not add hidden reasoning to durable storage to improve resume fidelity.

#### Agent Framework Workflow checkpoints are not adopted

Agent Framework also ships a Workflow checkpoint system with Cosmos-backed storage [R13]. It is deliberately not used here:

- Its checkpoints land at superstep boundaries, which is coarser than the per-tool commit boundary this design needs.
- Its storage uses `pickle` behind a restricted unpickler and is an explicit trust boundary [R13]; the task/operation/evidence records are plain JSON and already owner-scoped.
- Adopting it would reintroduce a workflow engine immediately after D02 removed one.

Record this as a considered rejection, not an oversight. Revisit only if the analyst runtime genuinely becomes a multi-executor graph.

### 9.3 Ownership, partitioning, and the capacity ledger

Use Cosmos ETag-based conditional writes and fencing for ownership [R5]. A process-local semaphore alone cannot protect against overlapping replicas or revisions.

Existing workspace records share the hierarchical partition `(tenantId, ownerObjectId, sessionId)`. Existing runtime-container records use `/id` as the partition key. Preserve those partition keys.

Capacity and ownership are two different things and live in two different commit domains. Do not conflate them.

| Concern   | Record                                                                                                    | Authority it carries                                                                             |
| --------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Capacity  | One lease-ledger document in the runtime container, holding at most five active entries                   | Permission to _start_ a task. Bounds deployment-wide concurrency.                                |
| Ownership | The task record in its workspace partition, carrying owner instance, fencing generation, and lease expiry | Permission to _act and commit_ for that task. This is the only authority a durable write checks. |

A conditional write is atomic for the document it updates [R5]. It does not extend across the ledger, a workspace partition, Redis, and Blob. Never describe this protocol as one transaction.

#### Claim order

1. **Acquire capacity.** Conditionally update the ledger to add an entry naming the task, the owning instance, and an expiry. Reject when five live entries already exist. Capacity is taken first so the limit can never be exceeded, even briefly.
2. **Claim ownership.** Conditionally update the task record: set the owning instance, increment the fencing generation, and set the lease expiry. Ownership is claimed second so it is never granted to a task the deployment cannot run.
3. **Dispatch.** Only after step 2 succeeds may the executor call the model, a source, or the sandbox.

Release in reverse: reach a terminal or released task state first, then remove the ledger entry. Renew both while running; losing either renewal stops new dispatch.

#### The window between the two commits is real

A crash between step 1 and step 2 leaves a ledger entry for a task nobody owns. This is an orphaned capacity reservation. It is bounded and self-healing, but it exists, and the design must reconcile it rather than deny it.

| Crash point                      | Consequence                                        | Reconciliation                                                                                                                                                 |
| -------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| After capacity, before ownership | One slot held, task not running and still eligible | Ledger entry expires, or is released on sight when its task is unclaimed or terminal. Capacity is temporarily reduced; no task is lost and no task runs twice. |
| After ownership, before dispatch | Task shows an owner that is gone                   | Task lease expires; the next claimant increments the generation, which fences the dead owner.                                                                  |
| During dispatch                  | Remote work may be in flight                       | Reclaim requires the §8.2 execution reconciliation, not expiry alone.                                                                                          |
| During release                   | Terminal task with a stale ledger entry            | Released on sight during the recovery scan.                                                                                                                    |

#### Fencing is what prevents duplicate work

The fencing generation on the task record, not the ledger, is what every durable write checks.

- Every model call, tool dispatch, operation commit, and publication carries the generation it was issued under.
- A durable write whose generation is lower than the task's current generation is rejected. A rejected write must not be retried under a new generation by the stale executor; that executor stops.
- Claiming capacity does not authorize any commit. An executor holding a ledger entry but not the current generation has no authority at all.
- Losing lease renewal stops new dispatch immediately, before the lease is known to have expired elsewhere.

This is why capacity is allowed a bounded leak while ownership is not: a lost slot costs throughput, a lost fence would cost correctness.

Engineering starting defaults: 30-second leases on both records, renewal every 10 seconds, and a recovery scan at most 10 seconds apart while healthy. These are tunable parameters, not Azure guarantees.

Expiry is a comparison the application performs against a stored timestamp under an ETag-conditional write. It is never a Cosmos TTL deletion: item TTL is ignored unless the container enables TTL [R9], and the `runtime` container does not. If any record placed there is expected to disappear on its own, enable container TTL deliberately and treat deletion as cleanup, never as the mechanism that releases a lease or slot.

Every task carries a maximum wall-clock execution budget and a per-phase no-progress watchdog. A renewed lease proves an owner is alive; it does not prove the task is progressing. On exceeding either bound, stop dispatching new work for that task and finish it as an explicit failure or partial result with what was durably produced. Release its capacity only once any in-flight execution is known stopped under the §8.2 rules, so a stuck task cannot hold capacity indefinitely and a released slot never implies abandoned remote work. Engineering starting points, to be tuned with measured distributions: a 30-minute total task budget and a 10-minute no-progress bound, both configurable and reported.

Maintain a discoverable pending-work projection without making it canonical. Commit the task first, update its locator/projection, and reconcile projection gaps on startup and bounded sweeps. A crash after durable admission but before notification must not strand a task. Measure the RU cost and scan latency rather than polling complete session histories.

### 9.4 Recovery behavior

| Interruption                         | Required behavior                                                                                                                                                                                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Browser/SSE disconnect               | Execution continues. Reconnect restores canonical state and retained events without creating a new task.                                                                                                                                                  |
| API process/revision restart         | Another healthy supervisor claims eligible expired work and restores the last committed boundary.                                                                                                                                                         |
| Committed operation already exists   | Reuse its immutable result; do not dispatch it again.                                                                                                                                                                                                     |
| Source request outcome is unknown    | Look for a durable result/provider correlation first. If it cannot be recovered, a bounded read-only retry may be needed; record the new observation time and reconcile consistency.                                                                      |
| Sandbox command may still be running | Classify from the sandbox filesystem using the §8.2 receipt rules, not from the application timeout. Keep polling a live heartbeat; terminate deliberately before retrying an unknown execution. Do not start a second copy because the API disconnected. |
| Sandbox lost                         | Recreate from the pinned image and persisted manifest; replay only unfinished or explicitly invalidated computation.                                                                                                                                      |
| Redis unavailable                    | Continue from Cosmos/Blob; expose degraded streaming and canonical progress/final results. Do not block completion on event delivery.                                                                                                                     |
| Cosmos/Blob unavailable              | Do not acknowledge durable acceptance/commit or start unrecoverable new effects. Surface degraded readiness and retain bounded retry state.                                                                                                               |
| User grant expired/revoked           | Stop protected access and require the existing owner-bound authorization flow; never fall back to an application identity.                                                                                                                                |
| Shutdown during publication          | Reconcile content hash/version and publication record; never publish a duplicate artifact version as a new result.                                                                                                                                        |

There is no claim of exactly-once network execution or a cross-service transaction. The promise is no repeat of committed work, fenced state changes, and idempotent/reconciled publication.

### 9.5 Controls and recovery objective

Persist cancellation and steering before acknowledging them. Apply each command once in sequence, checking cancellation before new model/tool work and before publication. If remote work cannot be stopped or reconciled, expose `cancelling` or the existing failure outcome rather than claiming cancellation succeeded.

Steering can invalidate affected evidence/output requirements, but does not discard unrelated completed work. A changed filter or freshness request must not reuse the old query fingerprint.

For an interrupted task not blocked on user authorization or clarification, measure recovery-start latency from application/dependency readiness to the first fenced restore/reconciliation action. Lease waiting, detection, and claim time are included in the 60-second objective; do not reset the clock after a lease expires. Recovery start is not a claim that the entire analysis finishes within 60 seconds. Also record failure-to-recovery duration, dependency downtime, and time to the next useful completed step.

## 10. API and UX compatibility

Use the existing task API as the canonical admission surface, rather than adding another competing run abstraction.

| Surface                                               | Target behavior                                                                                                                                 |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/sessions/{id}/messages`                    | Preserve canonical messages and idempotency.                                                                                                    |
| `POST /api/sessions/{id}/tasks`                       | Admit every analytical/conversational run to the same engine and return a task reference promptly.                                              |
| `GET /api/tasks/{id}`                                 | Canonical queued/running/blocked/terminal state; reading state must not be the only recovery trigger.                                           |
| `GET /api/tasks/{id}/events`                          | Existing SSE surface, now used for all tasks. Preserve cursors and attempt-aware event ordering.                                                |
| Task steer/cancel routes                              | Preserve explicit controls and idempotent acknowledgments.                                                                                      |
| History, provenance, artifact listing/download routes | Preserve existing URLs and owner checks; expand provenance beyond the old bounded handoff list using a compatible versioned/paginated contract. |
| Existing session `/chat` route                        | Temporary compatibility adapter to the same engine if needed. It must not retain an independent model loop or a handoff path.                   |

Remove `deepAnalysis`, the menu choice, automatic attachment-based mode switching, `run_deep_analysis`, and "starting deeper analysis" transitions from the new experience.

Keep uploads, quarantine/scanning checks, source evidence, the artifact panel, downloads, steering, cancellation, saved sessions, and optional-pack readiness messaging.

Use truthful progress such as queued, reading data, reusing evidence, computing, generating, validating, publishing, and recovering. Do not generate progress with extra model calls.

On event loss, return a canonical snapshot and mark the gap. Do not fabricate missing token deltas. Do not reset a task or duplicate its final answer when the Redis stream or attempt changes.

## 11. Model, budgets, and safety invariants

- Pin the current served model/snapshot and `gpt-5.6-terra-medium-v1` profile for baseline/candidate comparison.
- Keep model-contract validation and tokenizer/context safeguards. Do not silently move from the analysis output allowance to the smaller interactive allowance.
- Record model calls, actual tokens, reasoning/output settings, retries, compaction, and repair rounds. A maximum-token setting is not evidence of actual token consumption.
- Retain bounded tool/iteration/repair budgets as protection against runaway work. Tune only after observed distributions and no-regression evidence; do not make a low universal query cap the optimization.
- No source write tools, unsolicited external communication, or model-generated access-policy changes are introduced.
- Keep tenant/owner isolation, delegated Fabric authorization, readiness checks, and audit/provenance behavior across direct graph, ontology MCP, and semantic-model routes.
- Cached evidence is not an authorization bypass. Recheck current application/grant state before protected reuse; never infer continuing source permission solely from a cached success or an unexpired token. Document the limits of remote revocation detection.
- Keep queries, schemas, business values, tokens, and raw responses out of general-purpose telemetry. Owner-scoped evidence storage is separate from operational logs.

## 12. Measurement and acceptance

### 12.1 Instrument the critical path first

Use the existing telemetry stack. Add correlated spans/counters around:

| Stage            | Required measurements                                                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Admission/queue  | Durable submission time, queue wait, eligible-to-start delay, active/queued tasks, conversation contention.                                  |
| Model            | Per-call duration, time to first useful output, actual tokens, tool turns, retries, compaction, and repair rounds.                           |
| Fabric transport | Auth acquisition, connection/session initialization, `list_tools`, schema discovery, relationship discovery, and provider request count.     |
| Fabric execution | Route, query purpose/fingerprint, duration, result bytes/rows, completeness, retry/throttle counts, cache hits/misses, coalesced duplicates. |
| Sandbox          | Allocation/resume, input upload bytes/time, execution, output download, reconstruction, and disposal.                                        |
| Artifacts        | Build/render, deterministic validation, repair, durable publication, and final-message commit.                                               |
| Recovery         | Failure detection, lease wait, dependency readiness, reconciliation, resume start, and stale-fence rejections.                               |

Count actual provider requests separately from agent-visible tool calls. One tool invocation can hide multiple transport/discovery calls. Graph query duration also depends on model size, density, and shared capacity pressure [R4], so record the source-side conditions alongside the timing rather than attributing every change to application code.

Compute end-to-end latency from wall-clock timestamps, including queueing and publication. Do not add parallel span durations and call the sum elapsed time. First token is a secondary UX metric, not the completion objective.

Existing `ChatQueryStep` timestamps and query hashes are useful input, but do not substitute them for missing model/sandbox/publication timing.

### 12.2 Comparable baseline

Capture a reviewed baseline from the currently working system before replacing it.

- Use representative approved questions and fixed data/source refresh identities where available.
- Match model/snapshot, reasoning, capacity, region, images, enabled packs, question mix, and load.
- Include lookup, analysis without files, and artifact-producing analysis; separate XLSX, dashboard, and combined requests within the artifact class.
- Exercise one and five concurrent tasks, cold metadata/new sandbox conditions, and warm in-task operations.
- Record graph/source refresh times; cached task evidence does not create a transactional snapshot across Fabric engines.
- Use interleaved or paired runs where practical to reduce time-of-day/provider-load bias.
- Start with a small screening run. For the release comparison, collect at least 50 attempts per arm for each workload class and load level, preserve the workload mix, and report warm/cold breakdowns and uncertainty. Extend sampling if the p95 estimate is inconclusive.
- Obtain explicit approval for the cost and data use of live evaluation before running it.

Report failures, cancellations, timeouts, and incomplete artifacts separately and in the completion denominator. Do not make the candidate look faster by dropping slow/failed attempts or excluding queue time.

Persist both baseline and candidate engine/prompt/contract hashes in a comparison manifest. Prompt hashes may differ because prompt/tool consolidation is part of the change; that difference must be explicit. Do not bypass identity validation or compare unrelated historical reports. Update the existing evaluator with a deliberate comparison contract rather than making every identity field optional.

### 12.3 Performance gates

For each workload class at the approved load levels:

These requirements are hard blockers. They are never waived, traded, or renegotiated to make a release possible:

- Successful completion rate is no lower than the reviewed baseline and meets the existing 95% floor.
- Artifact completion time ends only after all requested artifacts are available and the final answer is durable.
- No task class is hidden inside a faster overall weighted average; every class is reported separately.
- Identical same-task source operations produce one provider execution, excluding documented recovery retries with uncertain outcomes.
- Warm unchanged metadata does not trigger redundant discovery, and non-Fabric requests make no Fabric calls.
- Recovery begins within the approved 60-second healthy-recovery window.
- No class is slower than its recorded baseline on p50 or p95.

The latency objective is measured per workload class at the approved load levels:

- `candidate_p50 <= 0.70 * baseline_p50`.
- `candidate_p95 <= 0.70 * baseline_p95`.

If a class meets the objective, record it. If a class misses it, that class alone is reported as not meeting the objective; it does not retroactively invalidate classes that met it, and it does not by itself cancel the revamp. Instead, produce a decision record containing the measured p50/p95 with their uncertainty, the stage breakdown showing where the remaining time is spent, which of those stages is reachable without changing the model or reasoning profile fixed by D09, and the residual optimization options with their estimated effect and risk.

The decision on a missing class is yours, made against that record: accept and release with the actual measured numbers stated, continue optimizing within the approved constraints, or reopen a decision such as D09 explicitly. Do not relax this gate silently, reduce insight scope, weaken the model, bypass validators, or restate an unmet objective as met. A class that missed the objective is described by its measured result, never as a success.

### 12.4 Quality gates

Reuse and extend the current [scenario corpus](../evals/scenario-corpus.md), not a replacement text-only acceptance suite.

| Dimension           | Release requirement                                                                                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Numeric correctness | Every deterministic case passes its predeclared business-appropriate tolerance; correct units, grain, time range, filters, and denominators.                                             |
| Answer completeness | Every requested sub-question and output requirement is fulfilled or explicitly blocked with a truthful reason.                                                                           |
| Insight quality     | Blind paired review against approved baseline outputs: factual grounding, material insight coverage, relevance, and actionable interpretation are not worse. Different prose is allowed. |
| Fabric semantics    | No dropped groups, invented value mappings, unsupported time-series claims, unauthorized source substitutions, or use of an incomplete preview as the full population.                   |
| Provenance          | Every important quantitative claim resolves to immutable evidence and any deterministic derivation.                                                                                      |
| Artifacts           | Existing file, formula, offline HTML, rendering, integrity, and publication requirements remain passing for enabled features.                                                            |
| Isolation/recovery  | Authorization, owner isolation, cancellation, reconnect, fencing, idempotency, and artifact integrity invariants pass every deterministic run.                                           |

Preserve the existing judge floor of median at least 4 and no score below 3, but do not treat that floor alone as "no regression." Predeclare the rubric and critical expected findings, review disagreements, and require no observed degradation against the paired baseline. Finite evaluations cannot prove unchanged quality for every possible future request; document that limitation.

The existing evaluator permits some stochastic regression and primarily blocks latency degradation. That policy is insufficient for this revamp's stronger improvement objective and must be extended explicitly.

### 12.5 Required regression scenarios

| ID  | Scenario and required result                                                                                                                                                                                                                                                                                      |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 | Plain conversation: same engine, no Fabric discovery and no sandbox allocation.                                                                                                                                                                                                                                   |
| A02 | Narrow graph lookup: grounded answer without unrelated exploratory calls.                                                                                                                                                                                                                                         |
| A03 | Repeated identical query within a task, including simultaneous requests: one provider execution and shared committed evidence.                                                                                                                                                                                    |
| A04 | New task, changed authorization, source/schema change, changed filter, or explicit refresh: no invalid result reuse.                                                                                                                                                                                              |
| A05 | Second task, same user and unchanged source: metadata is reused without rediscovery while business values are fetched fresh; a different principal shares neither.                                                                                                                                                |
| A06 | Unknown/ambiguous stored values and synonyms: targeted resolution or clarification, not guessed mappings.                                                                                                                                                                                                         |
| A07 | Multi-part ratios/rankings with missing relationships and duplicate edges: correct groups and denominators after consolidation.                                                                                                                                                                                   |
| A08 | Time-series/semantic-model request: use only supported configured capabilities and authoritative measures.                                                                                                                                                                                                        |
| A09 | Result exceeds model preview budget: retain complete evidence; source-side truncation is surfaced and cannot pass full-population acceptance.                                                                                                                                                                     |
| A10 | More than ten useful query results: preserve complete provenance and make required evidence available without the old handoff truncation.                                                                                                                                                                         |
| A11 | XLSX-only request: correct validated workbook, no unsolicited dashboard.                                                                                                                                                                                                                                          |
| A12 | Dashboard/XLSX combined request: consistent figures, required outputs, offline dashboard behavior, and evidence links.                                                                                                                                                                                            |
| A13 | Render/validation failure: repair the failed artifact without unnecessary data recollection or relaxed output requirements.                                                                                                                                                                                       |
| A14 | Browser disconnect, Redis outage, and stream expiry: task continues and history/final artifacts restore.                                                                                                                                                                                                          |
| A15 | Crash after submission, after query return, after Blob write, and during publication: recover or reconcile without duplicate committed effects.                                                                                                                                                                   |
| A16 | Crash between capacity acquisition and ownership claim, lease expiry, stale replica, and overlapping deployment revisions: the orphaned ledger entry is reconciled, stale-generation writes are rejected, one executing task per conversation holds, and the ledger never exceeds five active entries.            |
| A17 | Sandbox removed and API `_files` state lost: rebuild from durable manifests, not process memory.                                                                                                                                                                                                                  |
| A18 | Connection dropped mid-execution: the pre-assigned execution ID and in-sandbox receipt classify the run as not-started, running, unknown, or completed; a completed execution is reused rather than repeated.                                                                                                     |
| A19 | Watchdog expiry with an unknown execution: cooperative cancel, then confirmed sandbox deletion, and only then capacity release. Suspend is never used as cancellation.                                                                                                                                            |
| A20 | Steering/cancel during query, generation, and publication: ordered controls, no false completion, no repeat command application.                                                                                                                                                                                  |
| A21 | Revoked/expired grants, schema drift, unsupported tool contracts, and authorization-blocked head-of-conversation task: fail closed without blocking other conversations.                                                                                                                                          |
| A22 | Old sessions/artifacts and old in-flight tasks during cutover/rollback: ownership, history, and engine-version isolation preserved.                                                                                                                                                                               |
| A23 | Restore against a changed model, prompt, or tool surface: the incompatible session is not silently reused; the task is finished truthfully or restarted with a new session lineage.                                                                                                                               |
| A24 | Tool call recorded in history with no committed operation: reconciled from the operation record as uncertain, never replayed as if it had committed and never reported as completed.                                                                                                                              |
| A25 | Task exceeding its wall-clock budget or no-progress bound: truthful failure or partial result, durable outputs retained, and capacity released only once execution is known stopped.                                                                                                                              |
| A26 | Queue saturation by one owner: fair selection across owners, bounded depth with a clear retry-later rejection, and no indefinitely pending task.                                                                                                                                                                  |
| A27 | Source call reaching its application-owned deadline or the engine timeout: the outcome is handled as §9.4 uncertain, never reported as zero rows.                                                                                                                                                                 |
| A28 | Result beyond the documented 64 MB truncation: marked `sourceResultIncomplete`, blocked from full-population claims, and completed only through validated disjoint partitions. A large but untruncated result is not downgraded, and a result of unknown completeness is resolved rather than assumed either way. |

## 13. Code and deployment change map

These are implementation work items, not changes performed by this document.

| Surface                                                                                               | Required work                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/api/src/eda_api/main.py`, configuration and dependencies                                        | Own the single runtime/supervisor lifespan, shared clients, active-task limits, readiness, graceful shutdown, and recovery. Remove Hosted analysis-client initialization.                 |
| `apps/api/src/eda_api/chat/`, `task_service.py`, routes                                               | Consolidate admission/context/provenance; eliminate query-then-handoff; preserve existing owner-scoped endpoints and idempotency.                                                         |
| `apps/api/src/eda_api/fabric_auth/` and `services/worker/src/eda_worker/fabric/`                      | Consolidate provider/query adapters without moving credentials into the sandbox or creating circular imports. Preserve current optional provider support.                                 |
| `services/worker/src/eda_worker/agent/`, `model/`, `history/`, `context/`, tools and artifact modules | Extract reusable analytical behavior into an importable analyst library; do not throw away quality-producing logic with the worker host.                                                  |
| Proposed `packages/analyst/src/eda_analyst/`                                                          | Shared runtime, capability adapters, output contract, finalization, and model integration. No independently hosted server. API-facing dependencies are injected through shared contracts. |
| `services/worker/.../hosted_server.py`, `hosted_workflow.py`, host entrypoints                        | Retire after durable behavior is replaced and migration drained. Do not merely move the same two-path workflow into the API under new names.                                              |
| `packages/runtime-state/`                                                                             | Versioned task/operation/evidence contracts, queued state, leases/fences, conversation ordering, the shared capacity ledger, and crash-safe projection repair.                            |
| `services/sandbox/` and its adapter                                                                   | Persisted manifests, operation reconciliation, input deduplication, stable output boundaries, and recovery from lost sandbox state. Preserve image/tooling contracts.                     |
| `packages/artifacts/`, provenance and storage                                                         | Retain validators and hash-bound publication; complete query evidence and compatible provenance pagination.                                                                               |
| `apps/web/src/chat/Composer.tsx`, `layout/DesktopWorkspace.tsx`, API client/state                     | Remove mode flags and dual handlers; use the same task stream for all requests; retain attachments, artifacts, history, accessibility, and controls.                                      |
| `packages/contracts/`                                                                                 | Additive/versioned contracts and regenerated TypeScript bindings; backward-compatible history reading.                                                                                    |
| `azure.yaml`, Dockerfiles, Python package manifests/lockfile                                          | Remove analysis `long-job` deployment and migrate reusable worker dependencies into the application/library image. Keep Foundry model/project dependencies actually used.                 |
| `infra/bicep/`, `infra/terraform/`                                                                    | Matching topology, identities/RBAC, minimum replica policy, health, telemetry, budgets, and removal of Hosted analysis runtime bindings. No DTS or session-pool replacement.              |
| Image/build/deploy/doctor/RBAC scripts                                                                | Remove analysis-worker assumptions and obsolete required settings. Preserve image digests, model/document contracts, supply-chain gates, and preflight checks.                            |
| Cleanup, document-contract publication, and acceptance utilities                                      | Migrate their imports/image references as needed. Removing the analysis worker must not orphan maintenance or acceptance operations that currently use its image.                         |
| Tests, runbooks, README                                                                               | Update behavior and recovery ownership honestly; retain coverage of artifacts, reconnect, Fabric, and optional packs rather than deleting failing tests.                                  |

The combined application needs the least-privilege roles required for model calls, sandbox control, and canonical storage. Retire the old analysis identity only after confirming no maintenance/acceptance resource still uses it. Preserve delegated end-user access for Fabric.

Maintain Bicep/Terraform parity. Keep service availability/preview acceptance and existing regional checks; do not claim ACA Sandboxes availability from Dynamic Sessions regional documentation [R7].

## 14. Implementation sequence and cutover

| Phase                     | Deliverable and exit condition                                                                                                                                                                              |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P0: baseline              | Correlated timing, representative current outputs, data/model identity, and approved evaluation cost. No unsupported latency claims.                                                                        |
| P1: query efficiency      | Shared Fabric adapter, lazy scoped metadata, exact-query reuse, complete result storage, and semantics-preserving query shapes. Existing runtime can exercise these components before topology replacement. |
| P2: unified runtime       | Shared analyst library, unified output contract, durable admission/supervision, leases/fences, recovery, and artifact pipeline. Fault scenarios pass before Hosted execution is removed.                    |
| P3: UX and infra          | One task experience, compatible routes/history, combined application image, matching IaC, migrated operational utilities, and updated readiness/runbooks.                                                   |
| P4: controlled comparison | Quality and performance gates at one/five active tasks; classify cold/warm behavior, failures, and cost. A topology-only improvement is not sufficient.                                                     |
| P5: cutover               | Route new submissions to the unified runtime, drain old work, preserve history/artifacts, and remove obsolete analysis runtime resources only after drain and rollback conditions are satisfied.            |

Pin `engineVersion` to each task. An old Hosted task must not be claimed by the new supervisor as if it were a new task. Keep the legacy runtime available temporarily for its existing work, including authorization-blocked tasks, until they finish or are handled through an explicitly validated migration/cancellation procedure.

Use backward-compatible reads and additive schema changes during migration. Do not rewrite or delete historical artifacts to fit the new layout.

Preserve the existing 30-day durable session/task retention policy and associated cleanup behavior. Metadata revalidation intervals, execution leases, and sandbox scratch lifetimes must not shorten published-artifact or conversation retention.

Rollback changes admission for new tasks; it does not make the old runtime understand new checkpoints. Keep the new runtime capable of draining its own tasks during rollback. Do not dispatch both engines for the same task.

Roll back when any of the following is observed after cutover, without waiting for a full release cycle:

| Trigger               | Threshold                                                                                                                                                                    |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Correctness           | Any reproduced wrong business value, dropped group, or wrong denominator traceable to the new runtime.                                                                       |
| Publication integrity | Any duplicated, unvalidated, or cross-owner artifact or evidence record.                                                                                                     |
| Completion            | Successful completion rate below the reviewed baseline or below the existing 95% floor over the observation window defined below.                                            |
| Latency               | Rolling 24-hour end-to-end p95 worse than the recorded baseline for any workload class, over at least 20 completed tasks in that class. A single slow task is not a trigger. |
| Recovery              | An interrupted task that is neither resumed nor truthfully failed, or a repeated committed effect.                                                                           |
| Capacity              | Repeated queue rejections or task-budget expiries at or below the approved five-task load.                                                                                   |

The observation window is the later of 7 consecutive days of real traffic and 30 completed tasks in every workload class. A quiet week does not count as a pass; extend the window until each class has enough completed work to judge.

Declare cutover successful only after that window passes with the §12.3 performance gates and §12.4 quality gates holding on real traffic, no rollback trigger fired, and old in-flight work fully drained. Only then remove the legacy analysis runtime resources.

Temporary migration coexistence is not a permanent fast/deep architecture. Remove obsolete flags, analysis hosts, configuration, and deployment dependencies after the controlled cutover.

Use the repository's existing pytest, frontend, contract-generation, sandbox, acceptance, and IaC-parity runners. Relevant starting suites include:

- `apps/api/tests/unit/chat/`, `apps/api/tests/unit/fabric_auth/`, task route/service/storage tests.
- `services/worker/tests/fabric/`, `agent/`, `history/`, `context/`, `sandbox/`, output-planning/finalization tests, migrated with their modules.
- `packages/runtime-state/tests/`, `packages/artifacts/tests/`, and existing provenance tests.
- `tests/acceptance/test_core_vertical_slice.py`, enabled document/Fabric acceptance, and existing reconnect/artifact E2E cases.
- `infra/bicep/tests/`, `infra/terraform/tests/`, and deployment-script tests.

Run targeted suites during implementation, then the existing release gates applicable to the changed topology. Do not introduce a new test framework or silently drop existing artifact/recovery coverage.

## 15. Accepted tradeoffs and evidence still required

| Item                           | Treatment                                                                                                                                                                                                                                                                                                            |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Always-running application     | Accepted for responsive admission/recovery. Idle and active charges depend on actual resource/network activity [R6]; a polling supervisor is not guaranteed to receive idle pricing.                                                                                                                                 |
| Application-owned recovery     | Accepted small amount of coordination. Do not pretend that removing Hosted Workflow also removes the need for leases, checkpoints, reconciliation, and lifecycle ownership.                                                                                                                                          |
| Task-scoped sandbox allocation | Accepted isolation/simplicity choice. Optimize allocation only after measuring it; do not assume it dominates query/model latency.                                                                                                                                                                                   |
| Retained Redis                 | Accepted delivery dependency, but execution and final state must tolerate its loss.                                                                                                                                                                                                                                  |
| Ontology preview               | Public documentation marks it preview without a production SLA recommendation [R2]. Preserve readiness checks and explicit deployment risk acceptance.                                                                                                                                                               |
| ACA Sandboxes preview          | Sandboxes are documented as Preview, while Dynamic Sessions is GA [R14]. D03 accepts that in exchange for per-sandbox lifecycle control. Record the risk explicitly rather than only noting the Ontology preview, and re-verify status before production cutover.                                                    |
| Sandbox SDK is a beta pin      | The inspected baseline pins `azure-containerapps-sandbox==0.1.0b4`, whose surface is described in §8.2. Treat the execution contract as version-bound: re-verify the available operations on any upgrade, because the §8.2 receipt design exists precisely because no command lookup or cancel API is offered today. |
| Mutable Fabric data            | Task evidence is an as-of observation, not a guaranteed cross-query/cross-engine transaction. Record refresh/as-of information and surface material inconsistencies.                                                                                                                                                 |
| Quality/performance assurance  | Baseline comparison is required. Current code inspection and existing local sandbox timings do not prove the target is met.                                                                                                                                                                                          |
| Resource sizing and cost       | Five active tasks is a demonstrated concurrency objective, not a claim that the current API CPU/memory allocation is sufficient. Measure headroom, capacity pressure, model/Fabric throttling, and cost per successful task.                                                                                         |

No private WorkIQ documents, internal-only endpoint details, business data, credentials, or private research excerpts are reproduced in this repository specification. Public references below are the platform grounding.

## 16. Definition of done

- Every request uses the same analyst engine; no deep-analysis UI/tool/handoff or separate analysis-worker deployment remains.
- ACA Sandboxes are the only generated-code execution backend, allocated lazily per task.
- Existing analytical quality, requested artifact formats, readiness, validation, provenance, and owner isolation remain intact.
- Fabric discovery/connection policies are unified, duplicate source work is suppressed, and complete evidence is separated from model previews.
- Query changes preserve business semantics, not just lower request counts.
- Browser disconnect, API restart, Redis loss, uncertain operations, stale leases, and sandbox loss have demonstrated recovery behavior.
- Five-task cross-conversation concurrency and one-active-task-per-conversation behavior hold across overlapping instances/revisions.
- Every hard blocker in §12.3 holds. These are not negotiable and have no exception path.
- Every workload class has a measured p50 and p95 recorded under the declared comparison conditions, with no observed quality or completion regression.
- Each class either meets the 30% objective, or carries a written §12.3 decision record with its measured result and an explicit acceptance. "Done" therefore means every class is measured and resolved, not that every class reached the target. A class released with an exception is described by its actual numbers wherever the result is reported.
- Existing history/artifacts remain readable, migration/rollback are safe, and Bicep/Terraform plus operational tooling match the final topology.
- Remaining unverified platform behavior or unmet gates are reported explicitly; none are represented as completed.

## 17. Public references

Retrieved on 2026-09-21. Platform documentation may change; recheck relevant contracts before implementation/deployment.

- **R1 - ACA Sandboxes overview and lifecycle:** https://sandboxes.azure.com/docs/sandboxes/ and https://sandboxes.azure.com/docs/sandboxes/sandbox/lifecycle
- **R2 - Fabric IQ integration, query routes, authentication, and preview limits:** https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq
- **R2a - Ontology MCP tools and preview contract:** https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-fabric-iq-ontology
- **R3 - Fabric GQL optimization:** https://learn.microsoft.com/en-us/fabric/graph/gql-query-performance
- **R4 - Fabric graph performance monitoring and capacity factors:** https://learn.microsoft.com/en-us/fabric/graph/monitor-graph-performance
- **R5 - Cosmos transaction boundaries and optimistic concurrency:** https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/database-transactions-optimistic-concurrency
- **R6 - ACA billing, minimum replicas, and idle/active usage:** https://learn.microsoft.com/en-us/azure/container-apps/billing
- **R7 - Dynamic Sessions overview, evaluated but not selected:** https://learn.microsoft.com/en-us/azure/container-apps/sessions
- **R8 - Fabric graph documented limits (hops, 64 MB truncation, 128 MB aggregation instability, 20-minute timeout, no result export):** https://learn.microsoft.com/en-us/fabric/graph/limitations
- **R9 - Cosmos TTL configuration; item TTL is ignored when container TTL is disabled:** https://learn.microsoft.com/azure/cosmos-db/how-to-time-to-live
- **R10 - Agent Framework Harness: composition, function invocation, and per-service-call history persistence:** https://learn.microsoft.com/en-us/agent-framework/concepts/harness
- **R11 - Agent Framework session serialization, restoration, ownership binding, and configuration coupling:** https://learn.microsoft.com/en-us/agent-framework/concepts/agents/conversations/session
- **R12 - Agent Framework pipeline layers and per-tool function middleware:** https://learn.microsoft.com/en-us/agent-framework/concepts/agents/agent-pipeline
- **R13 - Agent Framework Workflow checkpoints, superstep boundaries, and checkpoint-storage trust boundary; evaluated but not adopted:** https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints
- **R14 - Dynamic Sessions versus Sandboxes, including the GA and Preview status of each:** https://sandboxes.azure.com/docs/sandboxes/dynamic-sessions-vs-sandboxes
