# Release checklist

A release is accepted only by fresh executable evidence. Source presence, local tests, documentation, a same-tenant smoke, or a prior deployment cannot promote readiness.

## Scope

- [ ] Model profile is exactly `gpt-5.6-terra-medium-v1`, served model is `gpt-5.6-terra` snapshot `2026-07-09`, mode is standard, and effort is medium.
- [ ] Region is `southeastasia`; subscription, tenant, resource group, and acceptance principal are explicit.
- [ ] No analyst-model router, fallback deployment, alternate profile, or substitute estimator exists.
- [ ] Monitoring alerts remain disabled for the accepted demo profile.
- [ ] Optional Fabric provider intent is immutable and selects at most one of `semantic_model` or `ontology`.
- [ ] Document terms acceptance matches the exact committed lock when enabled.

## Architecture

- [ ] Every deployed Azure service maps to an approved design requirement.
- [ ] Bicep and Terraform represent the same Core and selected optional-pack resource inventory.
- [ ] Terraform uses explicit apply plus azd service deployment and never invokes azd provisioning.
- [ ] API is the only public application ingress; acceptance jobs expose no ingress.
- [ ] Generated code runs only in credential-free, default-deny task-scoped ACA Sandboxes.
- [ ] No ACA long-job worker or Dynamic Sessions pool is deployed.
- [ ] No sandbox main-stage identity, application credential, arbitrary network target, or controller shell is available.

## Product state

- [ ] Browser ownership derives only from the authenticated server-side principal.
- [ ] Canonical messages and task state are committed before the task is queued.
- [ ] A committed task is recovered by any API replica's supervisor, without the original browser or replica.
- [ ] Steering, cancellation, blocked authorization, reconnect, and idempotent publication pass deployed tests.
- [ ] Redis remains transient delivery state; Cosmos/Blob remain canonical product state, including the execution ledger.

## Artifacts

- [ ] No artifact version is ready before its deterministic validator passes.
- [ ] Ready-only retrieval enforces owner scope and immutable digest/version binding.
- [ ] Important claims resolve to source/query/artifact evidence without a separate provenance platform.
- [ ] Reproducibility bundles contain no credential, token, identity claim, local path, or hidden reasoning.

## Optional packs

- [ ] Disabled packs are absent from model tools, API capability responses, and UI actions.
- [ ] Fabric-ready exposes exactly five application capabilities and no provider MCP tool.
- [ ] Fabric readiness is real two-tenant evidence; same-tenant smoke cannot promote it.
- [ ] Ontology and semantic-model contracts, grants, catalogs, evidence, and readiness are never interchangeable.
- [ ] Fabric authorization/denial/timeout/malformed-output cases produce no model-knowledge substitute.
- [ ] Document readiness binds deployment, worker and sandbox image digests, lock, commit, bundles, benchmark, and cloud observations.
- [ ] A failed optional-pack gate leaves that pack hidden and does not falsify Core health.

## Security and supply chain

- [ ] API, worker, and sandbox image references are exact SHA-256 digests.
- [ ] Syft `1.49.0` generates image and lockfile CycloneDX SBOMs.
- [ ] Grype `0.116.0` scans the generated image SBOMs and Trivy `0.72.0` scans the exact images.
- [ ] All detected licenses are covered by explicit legal review; unknown, LGPL, GPL, or other unreviewed expressions remain blocked.
- [ ] Gitleaks `8.30.1` scans Git history and the working tree without broad artifact exclusions.
- [ ] Cosign `3.1.2` verifies keyless signatures against the expected OIDC issuer and certificate identity.
- [ ] There are no unwaived high/critical findings or expired/incomplete waivers.

## Evaluation

- [ ] Eval identity hashes are lowercase SHA-256 values and match the reviewed baseline exactly.
- [ ] The reviewed Terra baseline exists; pending or fabricated baseline data blocks release.
- [ ] Automatic runs alone determine routing, completion, judge, quality, provenance, and latency thresholds.
- [ ] Forced compatibility probes cannot enter or improve automatic denominators.
- [ ] Deterministic invariants, numeric tolerances, Fabric routing/no-substitute controls, and ontology traces pass every required run.
- [ ] Prompt, tool description/source guide, model options, provider contract, or compaction changes have fresh regression evidence.
- [ ] Reports contain hashes, metric IDs, statuses, durations, counts, and run IDs only.

## Deployment and teardown

- [ ] Bicep clean deployment passes all enabled gates.
- [ ] Terraform clean deployment independently passes the same enabled gates.
- [ ] Each matrix cell uses fresh deployment-specific eval evidence.
- [ ] Each cell uses disposable app registrations and a resource group that did not exist before the run.
- [ ] Teardown runs even after deployment or gate failure.
- [ ] IaC-native destruction completes, disposable app registrations are removed, the resource group is absent, and Resource Graph reports zero remaining resources.
- [ ] No release manifest is written unless both IaC cells pass and verify zero teardown.

## Public release

- [ ] Repository contains no proprietary skill body, acceptance secret, target UUID, token cache, private fixture, or cloud evidence content.
- [ ] Demo remains usable with Fabric and documents disabled.
- [ ] Preview limitations, support boundary, data boundary, and absence of contractual SLA are explicit.
- [ ] Release notes identify schema/API/model pins and any required operator revalidation.
