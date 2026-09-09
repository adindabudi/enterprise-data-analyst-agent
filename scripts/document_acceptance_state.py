from __future__ import annotations

from typing import Any

from eda_worker.acceptance.documents import (
    DocumentAcceptanceObservations,
    DocumentImageContract,
    document_contract_sha256,
    document_evidence_sha256,
)
from eda_worker.documents.readiness import DocumentFeatureRecord


def clean_cosmos_document(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if not key.startswith("_")}


def validate_document_promotion(
    *,
    feature: DocumentFeatureRecord,
    contract: DocumentImageContract,
    observations: DocumentAcceptanceObservations,
) -> DocumentFeatureRecord:
    contract_digest = document_contract_sha256(contract)
    evidence_digest = document_evidence_sha256(observations)
    expected = {
        "feature contract": (feature.contract_sha256, contract_digest),
        "observation contract": (observations.contract_sha256, contract_digest),
        "deployment": (observations.deployment_id, contract.deployment_id),
        "feature deployment": (feature.deployment_id, contract.deployment_id),
        "worker image": (observations.worker_image_digest, contract.worker_image_digest),
        "feature worker image": (feature.worker_image_digest, contract.worker_image_digest),
        "sandbox image": (observations.sandbox_image_digest, contract.sandbox_image_digest),
        "feature sandbox image": (feature.sandbox_image_digest, contract.sandbox_image_digest),
        "lock": (observations.lock_sha256, contract.lock_sha256),
        "feature lock": (feature.lock_sha256, contract.lock_sha256),
        "skill commit": (observations.commit, contract.commit),
        "feature skill commit": (feature.commit, contract.commit),
        "bundles": (observations.bundles, contract.bundles),
        "feature bundles": (feature.bundles, contract.bundles),
    }
    mismatches = [name for name, (actual, required) in expected.items() if actual != required]
    if mismatches:
        raise ValueError(f"Document Pack promotion mismatch: {', '.join(mismatches)}")
    if feature.state == "ready":
        if feature.evidence_sha256 != evidence_digest:
            raise ValueError("Document Pack is already ready with different evidence")
        return feature
    if feature.state != "configured" or feature.evidence_sha256 is not None:
        raise ValueError("Document Pack feature is not in promotable configured state")
    return DocumentFeatureRecord.model_validate(
        {
            **feature.model_dump(mode="json", by_alias=True),
            "state": "ready",
            "evidenceSha256": evidence_digest,
            "verifiedAt": observations.observed_at,
        }
    )
