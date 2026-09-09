# Sandbox single-image baseline

## Local evidence

- Image: `eda-sandbox:test`
- Image digest: `sha256:b3994d1569a21ed00974e834d188ac4f71ff56afb3e56a0c5b2b80ff9d4c8a25`
- Source state: working tree based on `445f083`; the cloud driver records the exact committed fixture revision when executed.
- Pool API version under test: `2025-02-02-preview`
- Intended region: Southeast Asia
- Session profile: 2 vCPU, 4 GiB, egress disabled, managed identity available only during image initialization
- Local concurrency measured: one container-backed Core vertical slice
- Local corpus: FY2026 workbook, recalculated XLSX, self-contained HTML and desktop/mobile screenshots, SVG/PNG chart, Mermaid source/SVG/PNG, manifest, and reproducibility bundle
- Local result: image build passed, 36 sandbox/artifact tests passed, and the Core vertical slice passed in approximately 8-10 seconds on the development host
- Local OOM/cross-session conclusion: no local OOM; cross-session isolation is not claimed from a single-container run

## Cloud gate

The current dual-image Document Pack benchmark has not been run. No retained mode-0600 benchmark artifact exists for the worker/sandbox digests and immutable document contract now in source. Prior unretained values are not release evidence and cannot promote `feature:documents`.

Run the shared driver against the deployed Southeast Asia pool with `--include-documents`, retain its sanitized mode-0600 output, bind its SHA-256 into `DocumentAcceptanceObservations`, and pass `make acceptance-documents`. Only that gate may update this section with measured values.

## Decision rule

The one-pool decision remains pending current measured evidence.

- Keep one pool only when all 13 cloud sessions pass, every peak RSS value is below 3.2 GiB, there is no OOM or isolation failure, and stop P95 is at most 15 seconds.
- If only document rendering exceeds resource objectives, first reduce document concurrency and rerun the same corpus.
- Add a second pool only through a reviewed design amendment when reduced document concurrency still misses the pilot objective.

Allocation P95 has no release threshold before pilot, but it must still be recorded. The runner tolerates bounded health-allocation 429s while the pool scales; production capability calls retain their shorter retry budget.
