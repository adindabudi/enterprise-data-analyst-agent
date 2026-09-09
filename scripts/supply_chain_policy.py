from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import cast


class SupplyChainPolicyError(ValueError):
    pass


def load_waivers(path: Path) -> list[dict[str, object]]:
    document: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, list):
        return [object_value(item, "waiver entry") for item in cast(list[object], document)]
    if isinstance(document, dict):
        mapping = cast(dict[str, object], document)
        values = mapping.get("waivers")
        if isinstance(values, list):
            return [object_value(item, "waiver entry") for item in cast(list[object], values)]
    raise SupplyChainPolicyError("waiver file must contain a waivers array")


def validate_vulnerabilities(
    findings: Sequence[Mapping[str, object]],
    waivers: Sequence[Mapping[str, object]],
    *,
    today: str,
) -> None:
    current_date = parse_date(today, "today")
    waiver_by_id: dict[str, Mapping[str, object]] = {}
    for waiver in waivers:
        identifier = string_value(waiver.get("id"), "waiver id")
        ticket = string_value(waiver.get("ticket"), "waiver ticket")
        if not ticket:
            raise SupplyChainPolicyError("waiver ticket is required")
        expiry = parse_date(string_value(waiver.get("expires"), "waiver expiry"), "waiver expiry")
        if expiry < current_date:
            raise SupplyChainPolicyError(f"waiver expired: {identifier}")
        waiver_by_id[identifier] = waiver
    for finding in findings:
        severity = string_value(finding.get("severity"), "finding severity").casefold()
        if severity not in {"critical", "high"}:
            continue
        identifier = string_value(finding.get("id"), "finding id")
        if identifier not in waiver_by_id:
            raise SupplyChainPolicyError(f"unwaived {severity} vulnerability: {identifier}")


def normalize_vulnerability_findings(documents: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for document in documents:
        if "matches" in document:
            matches = list_value(document.get("matches"), "grype matches")
            for match in matches:
                entry = object_value(match, "grype match")
                vulnerability = object_value(entry.get("vulnerability"), "grype vulnerability")
                findings.append(
                    {
                        "id": string_value(vulnerability.get("id"), "grype vulnerability id"),
                        "severity": string_value(vulnerability.get("severity"), "grype vulnerability severity"),
                    }
                )
            continue
        if "Results" in document:
            results = list_value(document.get("Results"), "trivy results")
            for result in results:
                result_entry = object_value(result, "trivy result")
                vulnerabilities = result_entry.get("Vulnerabilities")
                if vulnerabilities is None:
                    continue
                for vulnerability in list_value(vulnerabilities, "trivy vulnerabilities"):
                    item = object_value(vulnerability, "trivy vulnerability")
                    findings.append(
                        {
                            "id": string_value(item.get("VulnerabilityID"), "trivy vulnerability id"),
                            "severity": string_value(item.get("Severity"), "trivy vulnerability severity"),
                        }
                    )
            continue
        if isinstance(document, dict) and {"id", "severity"} <= set(document):
            findings.append(
                {
                    "id": string_value(document.get("id"), "finding id"),
                    "severity": string_value(document.get("severity"), "finding severity"),
                }
            )
            continue
        raise SupplyChainPolicyError("unsupported vulnerability report format")
    return findings


def validate_digest_image_ref(value: str, *, label: str) -> str:
    if not re.fullmatch(r"[^:@]+(?:/[^:@]+)+@sha256:[0-9a-f]{64}", value):
        raise SupplyChainPolicyError(f"{label} image must be pinned by digest")
    return value


def parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SupplyChainPolicyError(f"invalid {label}") from error


def string_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SupplyChainPolicyError(f"invalid {label}")
    return value


def object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SupplyChainPolicyError(f"{label} must be an object")
    return cast(dict[str, object], value)


def list_value(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise SupplyChainPolicyError(f"{label} must be an array")
    return cast(list[object], value)
