from __future__ import annotations

from eda_api.config import Settings
from eda_api.readiness.models import (
    DocumentFeatureState,
    FabricFeatureState,
    FabricPackStatus,
    document_pack_status,
    fabric_pack_status,
)
from fastapi.testclient import TestClient


def test_readiness_fails_closed_with_explicit_blocked_components(client: TestClient) -> None:
    platform = client.get("/health/platform-ready")
    response = client.get("/health/ready")

    assert platform.status_code == 200
    assert platform.json() == {"status": "alive"}
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["components"] == {
        "cosmos": "configured",
        "blob": "configured",
        "redis": "configured",
        "foundry": "blocked",
        "hostedAgent": "blocked",
        "sandbox": "blocked",
        "auth": "blocked",
    }
    assert payload["featurePacks"]["core"] == "blocked"
    assert payload["featurePacks"]["fabric"] == "disabled"


def test_core_ready_configuration_requires_every_gate(settings: Settings) -> None:
    values = settings.model_dump(mode="python")
    values["core_ready"] = True

    try:
        Settings.model_validate(values)
    except ValueError as error:
        assert "every execution and federation gate" in str(error)
    else:
        raise AssertionError("incomplete Core readiness must be rejected")


def test_api_fabric_health_is_filtered_and_fail_closed() -> None:
    configured = FabricFeatureState.model_validate(
        {
            "id": "feature:fabric",
            "provider": "semantic_model",
            "state": "configured",
            "providerContractSha256": "a" * 64,
            "deploymentId": "deployment-20260724",
            "verifiedAt": "2026-07-24T00:00:00Z",
        }
    )
    ready = configured.model_copy(update={"state": "ready", "acceptance_evidence_sha256": "b" * 64})

    assert fabric_pack_status(enabled=False, feature=None) is FabricPackStatus.DISABLED
    assert fabric_pack_status(enabled=True, feature=None) is FabricPackStatus.FAILED
    assert fabric_pack_status(enabled=True, feature=configured) is FabricPackStatus.CONFIGURED
    assert fabric_pack_status(enabled=True, feature=ready) is FabricPackStatus.READY
    assert set(configured.model_dump(mode="json", by_alias=True)) == {
        "id",
        "provider",
        "state",
        "providerContractSha256",
        "deploymentId",
        "acceptanceEvidenceSha256",
        "verifiedAt",
    }

    ontology = FabricFeatureState.model_validate(
        {
            "id": "feature:fabric-ontology",
            "provider": "ontology",
            "state": "configured",
            "providerContractSha256": "c" * 64,
            "deploymentId": "deployment-20260724",
            "verifiedAt": "2026-07-24T00:00:00Z",
        }
    )
    assert fabric_pack_status(enabled=True, feature=ontology) is FabricPackStatus.CONFIGURED


def test_api_document_health_is_evidence_bound_and_filtered() -> None:
    configured = DocumentFeatureState.model_validate(
        {
            "state": "configured",
            "deploymentId": "deployment-20260724",
            "contractSha256": "c" * 64,
            "workerImageDigest": "sha256:" + "a" * 64,
            "sandboxImageDigest": "sha256:" + "f" * 64,
            "lockSha256": "d" * 64,
            "commit": "e" * 40,
            "bundles": {"docx": "1" * 64, "pdf": "2" * 64, "pptx": "3" * 64, "xlsx": "4" * 64},
            "verifiedAt": "2026-07-24T00:00:00Z",
        }
    )
    ready = configured.model_copy(update={"state": "ready", "evidence_sha256": "b" * 64})
    expected = {
        "deployment_id": "deployment-20260724",
        "worker_image_digest": "sha256:" + "a" * 64,
        "sandbox_image_digest": "sha256:" + "f" * 64,
    }

    assert document_pack_status(enabled=False, feature=None, **expected) is FabricPackStatus.DISABLED
    assert document_pack_status(enabled=True, feature=None, **expected) is FabricPackStatus.FAILED
    assert document_pack_status(enabled=True, feature=configured, **expected) is FabricPackStatus.CONFIGURED
    assert document_pack_status(enabled=True, feature=ready, **expected) is FabricPackStatus.READY
    assert (
        document_pack_status(enabled=True, feature=ready, **{**expected, "deployment_id": "other"})
        is FabricPackStatus.FAILED
    )
    serialized = configured.model_dump_json(by_alias=True)
    assert "skill" not in serialized.casefold()
    assert "license" not in serialized.casefold()
