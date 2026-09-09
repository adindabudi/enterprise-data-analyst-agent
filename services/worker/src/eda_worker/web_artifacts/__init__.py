"""Mandatory, independently licensed web-artifact skill support."""

from .readiness import WebArtifactReadiness, WebArtifactSettings, require_web_artifact_skill, web_artifact_readiness

__all__ = [
    "WebArtifactReadiness",
    "WebArtifactSettings",
    "require_web_artifact_skill",
    "web_artifact_readiness",
]
