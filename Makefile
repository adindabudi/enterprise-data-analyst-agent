.PHONY: bootstrap check contracts format lint test typecheck integration e2e visual-check security acceptance-core acceptance-fabric acceptance-fabric-ontology fabric-ontology-smoke acceptance-documents parity iac-parity supply-chain-gate eval-gate release-gate test-storage test-storage-cloud acceptance-storage deployed-storage-gate deployed-core-gate test-harness model-contract sandbox-image sandbox-contract core-artifact-acceptance sandbox-benchmark document-vertical-slice document-benchmark

bootstrap:
	uv sync --all-packages --frozen
	npm ci
	PUPPETEER_SKIP_DOWNLOAD=true npm --prefix services/sandbox ci --ignore-scripts

.PHONY: source-notices
source-notices:
	mkdir -p .artifacts/source-sbom
	uv export --frozen --all-packages --no-dev --no-emit-workspace --no-hashes --format requirements-txt --output-file .artifacts/source-sbom/requirements.txt >/dev/null
	uv run python scripts/generate-python-sbom.py --requirements .artifacts/source-sbom/requirements.txt --output .artifacts/source-sbom/python-runtime.cdx.json
	npm sbom --omit=dev --package-lock-only --sbom-format cyclonedx >.artifacts/source-sbom/web-runtime.cdx.json
	npm --prefix services/sandbox sbom --omit=dev --package-lock-only --sbom-format cyclonedx >.artifacts/source-sbom/sandbox-runtime.cdx.json
	uv run python scripts/verify_license_allowlist.py --sbom-dir .artifacts/source-sbom --overrides docs/security/license-overrides.json --notices THIRD_PARTY_NOTICES.md

contracts:
	uv run python scripts/export-contracts.py
	npm run contracts:generate

format:
	uv run ruff format .
	uv run ruff check --fix .
	npm run format

lint:
	uv run ruff format --check .
	uv run ruff check .
	npm run format:check
	npm run lint

typecheck:
	uv run pyright
	npm run typecheck

test:
	uv run pytest -m "not integration and not cloud" --cov --cov-report=term-missing
	npm test

web-build:
	npm --workspace @eda/web run build
	rm -rf apps/api/static
	cp -R apps/web/dist apps/api/static

web-test:
	npm --workspace @eda/web test

check: contracts lint typecheck test
	git diff --exit-code -- packages/contracts/schema packages/contracts/typescript/src/generated

integration:
	@set -e; \
	trap 'docker compose down --remove-orphans' EXIT; \
	docker compose up -d --wait redis redis-health azurite; \
	uv run pytest -m integration apps/api/tests/integration tests/integration

test-storage:
	uv run pytest apps/api/tests/unit -q

test-storage-cloud:
	uv run pytest -m cloud apps/api/tests/cloud/test_auth_container.py apps/api/tests/cloud/test_storage_contract.py -q

acceptance-storage:
	EDA_RUN_DEFENDER_ACCEPTANCE=1 uv run pytest -m cloud apps/api/tests/cloud -q

test-harness:
	uv run pytest services/worker/tests/model services/worker/tests/tools services/worker/tests/history services/worker/tests/context services/worker/tests/agent -q

model-contract:
	@test -n "$$EDA_FOUNDRY_PROJECT_ENDPOINT" || (echo "FAIL: EDA_FOUNDRY_PROJECT_ENDPOINT is required" >&2; exit 1)
	@test -n "$$EDA_FOUNDRY_RESOURCE_ENDPOINT" || (echo "FAIL: EDA_FOUNDRY_RESOURCE_ENDPOINT is required" >&2; exit 1)
	@test -n "$$EDA_FOUNDRY_MODEL_DEPLOYMENT" || (echo "FAIL: EDA_FOUNDRY_MODEL_DEPLOYMENT is required" >&2; exit 1)
	@test -n "$$EDA_MODEL_PROFILE" || (echo "FAIL: EDA_MODEL_PROFILE is required" >&2; exit 1)
	mkdir -p .artifacts
	uv run python scripts/calibrate-tokenizer.py --endpoint "$$EDA_FOUNDRY_RESOURCE_ENDPOINT" --deployment "$$EDA_FOUNDRY_MODEL_DEPLOYMENT" --model-profile "$$EDA_MODEL_PROFILE" --output .artifacts/tokenizer-calibration.json
	uv run python scripts/check-model-contract.py --project-endpoint "$$EDA_FOUNDRY_PROJECT_ENDPOINT" --deployment "$$EDA_FOUNDRY_MODEL_DEPLOYMENT" --model-profile "$$EDA_MODEL_PROFILE" --output .artifacts/model-contract.json

sandbox-image:
	docker build -f services/sandbox/Dockerfile -t eda-sandbox:test .

sandbox-contract:
	EDA_SANDBOX_IMAGE=eda-sandbox:test uv run pytest services/sandbox/tests packages/artifacts/tests -q

core-artifact-acceptance:
	EDA_SANDBOX_IMAGE=eda-sandbox:test uv run pytest tests/acceptance/test_core_vertical_slice.py -q

sandbox-benchmark:
	uv run python scripts/run-sandbox-benchmark.py --output .artifacts/sandbox-benchmark.json

document-vertical-slice:
	uv run pytest tests/acceptance/test_document_pack.py -q

document-benchmark:
	uv run python scripts/run-sandbox-benchmark.py --include-documents --output .artifacts/document-benchmark.json

e2e: web-build
	npx playwright test --project=desktop --project=mobile tests/e2e/auth-bootstrap.spec.ts tests/e2e/workspace.spec.ts tests/e2e/reconnect.spec.ts tests/e2e/accessibility.spec.ts

visual-check: web-build
	npx playwright test tests/e2e/visual.spec.ts

security:
	$(MAKE) supply-chain-gate

acceptance-core:
	@test -n "$$EDA_SANDBOX_IMAGE" || (echo "FAIL: EDA_SANDBOX_IMAGE is required" >&2; exit 1)
	EDA_SANDBOX_IMAGE="$$EDA_SANDBOX_IMAGE" uv run pytest tests/acceptance/test_core_vertical_slice.py -q

acceptance-fabric:
	@./scripts/run-fabric-acceptance.sh

acceptance-fabric-ontology:
	@./scripts/run-fabric-ontology-acceptance.sh

fabric-ontology-smoke:
	@./scripts/run-fabric-ontology-smoke.sh

acceptance-documents:
	@./scripts/run-document-acceptance.sh

parity:
	$(MAKE) iac-parity

iac-parity:
	uv run pytest infra/terraform/tests scripts/tests/test_deploy_terraform_environment.py -q
	@if [ -n "$$IAC_PARITY_ARM" ] && [ -n "$$IAC_PARITY_TERRAFORM_PLAN" ]; then \
		arm_args=""; \
		for arm in $$IAC_PARITY_ARM; do \
			arm_args="$$arm_args --arm $$arm"; \
		done; \
		uv run python scripts/verify_iac_parity.py $$arm_args --terraform "$$IAC_PARITY_TERRAFORM_PLAN"; \
	else \
		echo "SKIP: set IAC_PARITY_ARM and IAC_PARITY_TERRAFORM_PLAN to run the offline parity comparator"; \
	fi
	@if command -v terraform >/dev/null 2>&1; then \
		terraform -chdir=infra/terraform fmt -check; \
		terraform -chdir=infra/terraform init -backend=false; \
		terraform -chdir=infra/terraform validate; \
	else \
		echo "SKIP: terraform unavailable"; \
	fi

supply-chain-gate:
	./scripts/run-supply-chain-gate.sh

eval-gate:
	@test -n "$$EDA_EVAL_RESULTS" || (echo "FAIL: EDA_EVAL_RESULTS must point to a Terra eval result object" >&2; exit 1)
	uv run python scripts/run-eval-suite.py --results "$$EDA_EVAL_RESULTS" --output .artifacts/eval-results-gpt-5.6-terra-medium-v1.json

release-gate:
	./scripts/run-clean-subscription-acceptance.sh

deployed-storage-gate:
	uv run python scripts/run-deployed-storage-gate.py apps/api/tests/cloud packages/runtime-state/tests/cloud -q

deployed-core-gate:
	uv run pytest tests/deployment -q
	$(MAKE) deployed-storage-gate
	$(MAKE) model-contract
	$(MAKE) sandbox-benchmark
	$(MAKE) acceptance-core
	BASE_URL="$${EDA_APP_URL}" npx playwright test --project=desktop --project=mobile tests/e2e/workspace.spec.ts tests/e2e/accessibility.spec.ts
