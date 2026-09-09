# Document Pack Acceptance

The Document Pack is optional and fail-closed. Image acquisition and hash verification establish only `configured`; only deployed generation, validation, rendering, publication, leak scans, and measured concurrency may promote `feature:documents` to `ready`.

## Terms acceptance

Review the exact committed `skills.lock.json` and the referenced terms. Record acceptance for the reviewed bytes only:

```bash
export EDA_DOCUMENT_TERMS_ACCEPTED="$(sha256sum skills.lock.json | cut -d' ' -f1)"
```

Any change to `skills.lock.json` changes this hash. Review the new lock, record a new acceptance value, and rebuild the worker image through the approved deployment process before attempting a real deployment acceptance.

## Build and configured state

Run prebuild verification before either application image is built:

```bash
DOCUMENTS_ENABLED=true ./scripts/doctor-documents.sh --phase prebuild
```

Both the worker and sandbox Dockerfiles acquire the pinned source inside a gated build stage. Acquired bodies are excluded from Git and the host Docker context. After image pinning, the doctor runs both images with networking disabled, requires identical bundles, and may emit a mode-0600 image contract containing only deployment, worker/sandbox digests, lock, commit, and bundle hashes. `publish-document-contract.py` stores that contract immutably and ETag-writes `feature:documents` as `configured`.

Do not interpret a successful build, doctor, local vertical slice, or file presence as readiness.

## Deployed evidence

Collect two mode-0600 files against the exact pinned deployment:

1. A raw sandbox benchmark from the shared driver:

   ```bash
   uv run python scripts/run-sandbox-benchmark.py \
     --include-documents \
     --output /secure/evidence/document-benchmark.json
   chmod 600 /secure/evidence/document-benchmark.json
   ```

2. `DocumentAcceptanceObservations` from the deployed same-origin harness. It must prove the wrong-terms image build failed, the baked acquisition matches the lock, two published generations of each format have distinct immutable hashes, all validation reports and previews exist, and no acquired body appears in application logs, App Insights, Cosmos, or Blob manifests. It must bind the benchmark file SHA-256 and the immutable image-contract digest.

The observation file contains hashes, counters, statuses, durations, and run IDs only. Document values, skill bodies, license text, scripts, endpoints, user identities, and task content are forbidden.

## Promotion

Select the isolated product-tenant Azure CLI directory at mode 0700. The configured acceptance principal must hold the explicit Cosmos data-plane assignment from Bicep. Export the deployment values and run:

```bash
export PRODUCT_AZURE_CONFIG_DIR=/secure/azure/product
export EDA_DOCUMENT_ACCEPTANCE_OBSERVATIONS=/secure/evidence/document-observations.json
export EDA_SANDBOX_BENCHMARK_RESULTS=/secure/evidence/document-benchmark.json
make acceptance-documents
```

The runner verifies the image contract, publishes `configured`, checks both evidence files, runs the local vertical slice and four cloud controls, and ETag-promotes only an exact matching feature record. It then restarts worker and API revisions and requires `/health/ready` to report `documents=ready`. A mismatched deployment, image, lock, commit, bundle, contract, benchmark, principal, or existing evidence fails without promotion.

## Disabled mode

Set `DOCUMENTS_ENABLED=false` to keep the pack disabled. The target then exits zero with exactly:

```text
SKIP: Document Pack intentionally disabled
```

This mode does not require terms acceptance or evidence. Confirm `/health/ready` reports `documents=disabled` and document skill tools remain absent from the harness.

## Failures and repairs

A terms mismatch means the environment value does not match the committed lock bytes; review the new lock before changing the value. An image-contract mismatch requires rebuilding and repinning both relevant images. A cloud-observation failure requires rerunning the deployed harness; never edit the observation file. A concurrency failure requires inspecting the content-free session metrics and first reducing document concurrency. A leak finding blocks promotion until the affected telemetry or storage path is corrected and rescanned.

If a skill script fails, use its bounded validation status and content-free failure code. Do not copy acquired scripts, license bodies, document contents, or raw stdout into logs or tickets. `configured` remains the correct state while any external fixture or cloud permission is unavailable.

Document generation repair remains capped at two model-guided attempts per artifact. After the second failed repair, reject the artifact with its validation report; do not introduce a separate retry loop or patch a published binary in place.
