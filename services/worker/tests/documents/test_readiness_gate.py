from __future__ import annotations

from agent_framework import SkillsProvider
from eda_worker.documents.readiness import (
    DocumentReadiness,
    DocumentReadinessStatus,
    ready_skills_provider,
)


def _provider() -> SkillsProvider:
    return SkillsProvider.from_paths([], script_extensions=(".py",))


def test_a_failed_pack_withholds_skills_instead_of_stopping_the_worker() -> None:
    assert ready_skills_provider(DocumentReadiness(status=DocumentReadinessStatus.FAILED), _provider()) is None


def test_an_unverified_pack_withholds_skills() -> None:
    for status in (DocumentReadinessStatus.DISABLED, DocumentReadinessStatus.CONFIGURED):
        assert ready_skills_provider(DocumentReadiness(status=status), _provider()) is None


def test_a_ready_pack_supplies_its_skills() -> None:
    provider = _provider()

    assert ready_skills_provider(DocumentReadiness(status=DocumentReadinessStatus.READY), provider) is provider
