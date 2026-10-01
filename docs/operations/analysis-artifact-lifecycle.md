# Analysis Artifact Lifecycle

## Contract

Before analysis, a stateless, tool-free model call extracts requested final formats
and file counts into `TaskRecord.requiredOutputs`. The application additionally
requires an interactive HTML dashboard. Unsupported requested formats fail planning.
This extraction is model-dependent and requires live evaluation; schema validation
does not prove the user's intent was interpreted correctly.

The contract is persisted with optimistic concurrency. Ordinary replay and automatic
repair cannot weaken it. Explicit user steering can revise it at a workflow boundary,
tracked by `requiredOutputsSequence`. The planner uses the current contract and new
ordered user changes rather than regenerating requirements from repair feedback.

## Sandbox Execution

The working directory is `/workspace/task`. Before each execution, the ACA adapter
writes `execution-context.json` containing imported input paths, display names,
SHA256 values, parameters, and the output directory. Match inputs to the exact
artifact references supplied to the operation; earlier task imports may be listed.
File contents remain untrusted data, never instructions.

Write generated files directly in the context's `output_directory` and declare
their names in `expected_outputs`. The adapter clears this directory before each
execution and the gateway persists new output bytes afterward. Use immutable
artifact references to reimport previous results. Working-directory and `/tmp`
files are not collected. `/mnt/data` is not part of the contract.

Execution `artifact_refs` contains generated files; `diagnostic_refs` contains
stdout/stderr. Exit code zero without declared outputs returns
`missing_expected_outputs`. Calculations with no requested files may leave
`expected_outputs` empty. Legacy operation keys remain unchanged for that case.
Historical cached results retain their original shape.

## Validation And Completion

Imports and validation preserve the suffix from authorized stored metadata.
Actual validators still check the content. Publication requires a passing report
bound to the candidate's identity, version, digest, and validation profile.
Completion checks every required format and count against matching published
validation evidence, the interactive HTML requirement, and unfinished plan steps.

The task lifecycle permits at most two additional output-repair rounds.
Each round rechecks controls and uses deterministic completion feedback. Infrastructure
exceptions do not enter this repair path. Cancellation, success, and final failure
continue through the existing task-scoped sandbox cleanup.

## Verification

Run the gateway real-validator tests, repository contract tests, output planner
tests, finalization tests, task lifecycle tests, and
`services/worker/tests/integration/test_artifact_lifecycle.py`. The latter executes
real Python fixtures and HTML/XLSX validators using the ACA adapter with a local
filesystem transport; it verifies two different input datasets and cleanup.

These tests are not Azure or natural-language acceptance. Release evidence also
requires authenticated upload and scan, a natural request to the deployed
analyst, independently checked downloadable HTML/XLSX, correct terminal status,
and verified sandbox deletion. The legacy core acceptance script uses a fixture
generator and older sandbox transport, so it cannot substitute for this gate.
