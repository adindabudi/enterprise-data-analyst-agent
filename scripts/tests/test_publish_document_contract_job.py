from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_document_contract_job_uses_existing_serverless_job_and_private_identity() -> None:
    source = (ROOT / "scripts/publish-document-contract-job.sh").read_text(encoding="utf-8")

    assert "az containerapp job start" in source
    assert '"$cleanup_job_id"' in source
    assert '--command "eda-worker"' in source
    assert "publish-documents" in source
    assert '"EDA_MANAGED_IDENTITY_CLIENT_ID=$worker_identity_client_id"' in source
    assert '"EDA_COSMOS_ENDPOINT=$cosmos_endpoint"' in source
    assert '"EDA_COSMOS_DATABASE=enterprise-data-analyst"' in source
    assert '"EDA_COSMOS_RUNTIME_CONTAINER=runtime"' in source
    assert "EDA_DOCUMENT_IMAGE_CONTRACT_BASE64" in source
    assert "az containerapp job execution show" in source
    assert '--name "$job_name"' in source
    assert '--job-execution-name "$execution_name"' in source
    assert "--job-name" not in source
    assert "account key" not in source.lower()
    assert "connection string" not in source.lower()
