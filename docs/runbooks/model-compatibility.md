# Model compatibility gate

The model compatibility gate promotes one exact deployment contract. It is a deployment check, not a runtime router.

## Required inputs

Set these values from the deployed environment:

- `EDA_FOUNDRY_PROJECT_ENDPOINT`: Foundry project endpoint used by `FoundryChatClient`.
- `EDA_FOUNDRY_RESOURCE_ENDPOINT`: resource endpoint that exposes the profile-bound input-token count API.
- `EDA_FOUNDRY_MODEL_DEPLOYMENT`: exact deployment name.
- `EDA_MODEL_PROFILE`: exact application profile ID. The active deployment uses `gpt-5.6-terra-medium-v1`.

Use Azure CLI authentication locally. A deployed smoke may additionally set `EDA_MANAGED_IDENTITY_CLIENT_ID`. Do not provide keys or switch authentication paths when the selected credential fails.

Run:

```bash
make model-contract
EDA_RUN_MODEL_COMPATIBILITY=1 uv run pytest -m cloud services/worker/tests/cloud/test_model_compatibility.py -q
```

The target writes `.artifacts/tokenizer-calibration.json` and `.artifacts/model-contract.json`. Both are deployment artifacts and must match the same deployment and profile.

## Failure classification

### HTTP 400

A 400 response is a profile or request-contract rejection. The probe does not retry it. Confirm the deployed model/version/SKU, selected profile, exact candidate fields, and Foundry project endpoint. Do not remove reasoning mode, change effort, select another wire shape, or choose another model to make the gate pass.

### HTTP 429 or 5xx

The probe retries only 429 and 5xx responses, for three total attempts with bounded backoff. Exhaustion fails the gate. Check quota, deployment health, regional service health, and capacity before rerunning the same profile.

### Missing evidence

HTTP success alone is insufficient. Terra requires streamed text, completed status, model/snapshot identity, input/output/reasoning usage, explicit standard-mode and medium-effort evidence, bounded output, and no service conversation ID. Missing or ignored fields fail closed.

### Stateless tool loop

The automatic probe must route through `inspect_artifact` and `validate_artifact` using the actual four Core tool schemas. The separate forced probe must force `inspect_artifact` only for the first iteration and then prove MAF reset required selection. A tool error, different sequence, retained forced choice, or conversation ID fails the gate. Forced evidence never substitutes for automatic-routing evidence.

### Tokenizer calibration

Calibration must use the selected profile's one approved count endpoint for every fixed corpus sample. A rejected sample, zero count, unavailable endpoint, changed count source, or deployment/profile mismatch fails promotion. Do not substitute a character estimator or a different token-count service.

## Artifact inspection

The contract may contain only profile identity, prompt hash/version, canonical request options and hash, package/source pins, field names, usage field names, and non-content evidence markers.

Before promotion, verify:

```bash
python -m json.tool .artifacts/model-contract.json >/dev/null
rg -n 'https?://|phase complete|tool route complete|encrypted_content|protected_data' .artifacts/model-contract.json
```

The `rg` command must return no matches. The artifact must not contain endpoints, credentials, prompt text, model output, tool output, raw responses, reasoning content, or encrypted/protected reasoning payloads.

## Rollback

If a new deployment cannot pass, keep or restore the previously pinned application revision together with its matching contract and calibration artifacts. Do not rewrite the prior contract for a new deployment. Do not automatically fall back to another model, snapshot, hosting provider, effort, reasoning mode, wire protocol, endpoint, credential, or authentication method.
