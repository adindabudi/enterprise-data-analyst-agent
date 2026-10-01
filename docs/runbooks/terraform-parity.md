# Terraform parity

Bicep is the authoritative infrastructure design. Terraform is a second deployment implementation of that design, not an independent topology or feature surface.

## Local checks

Use Terraform `1.15.8` with AzureRM `4.81.0`, AzAPI `2.11.0`, and AzureAD `3.9.0` from the committed lock file:

```bash
make iac-parity
TF_DATA_DIR=/tmp/eda-tf-data terraform -chdir=infra/terraform init -backend=false
TF_DATA_DIR=/tmp/eda-tf-data terraform -chdir=infra/terraform validate
```

The source-policy tests prove module composition and known property contracts. They do not prove deployment parity.

## Real comparison

A parity claim requires compiled Bicep deployment-set ARM JSON and a Terraform plan JSON for the same location, profile, model, optional-pack intent, identities, image digests, and resource-group boundary:

```bash
export IAC_PARITY_ARM=".artifacts/bicep-main.json .artifacts/bicep-sandbox-group.json"
export IAC_PARITY_TERRAFORM_PLAN=.artifacts/terraform-plan.json
make iac-parity
```

The comparator checks normalized resource types, multiplicity, SKU, network exposure, identity kind, role assignments, and diagnostics. A source test or successful `terraform validate` is not parity evidence.

## Diagnose a mismatch

1. Confirm both artifacts use the exact same input profile and optional-pack provider.
2. Confirm the Bicep input contains every separately deployed template, including the Sandbox group.
3. Confirm the Terraform JSON came from `terraform show -json` for the final image-dependent plan, not the foundation apply.
4. Compare the reported logical resource, then inspect the authoritative Bicep module first.
5. Correct Terraform to match Bicep unless a reviewed design change explicitly updates both paths.
6. Rerun exact-version validation, the focused module tests, and the real comparator.

Do not suppress, rename away, or allowlist a mismatch merely because both paths deploy. If Bicep and Terraform disagree and the design is unresolved, Bicep remains authoritative and the release stays blocked.

## Deployment bridge

Terraform environments use [scripts/deploy-terraform-environment.sh](../../scripts/deploy-terraform-environment.sh). The bridge:

- requires Terraform `1.15.8`, Terra, Southeast Asia, and explicit subscription/resource-group/principal values;
- creates the product and optional Fabric apps before writing a mode-0600 runtime variable file;
- builds worker and sandbox images remotely, creates the Sandbox disk image and group, then applies image-dependent jobs;
- maps only allowlisted nonsecret outputs into an isolated azd environment;
- runs one `azd deploy --all`, so the API's postdeploy hooks run once;
- never calls `azd provision` or `azd up` in the Terraform path;
- preserves deployed API/worker digests on the final apply;
- stores Terraform data/state under an isolated acceptance path rather than the repository default state file.

## Clean release

[scripts/run-clean-subscription-acceptance.sh](../../scripts/run-clean-subscription-acceptance.sh) runs Bicep first and Terraform second against one disposable subscription sequentially, or two distinct disposable subscriptions. It refuses pre-existing resource groups and Entra app names. Every cell runs the deployed Core, selected optional-pack, supply-chain, and fresh Terra eval gates.

Teardown is mandatory even after a failed deployment or gate. Bicep uses `azd down --purge --force`; Terraform uses explicit `terraform destroy`. Both then delete any still-owned resource group, remove disposable app registrations, and require the group to be absent and Azure Resource Graph to report zero remaining resources. A successful local plan or apply without this teardown evidence is not release parity.
