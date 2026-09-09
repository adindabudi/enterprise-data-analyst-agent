from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel

OpaqueEventId = Annotated[str, StringConstraints(pattern=r"^evt_[A-Za-z0-9_-]{8,}$")]
OpaqueSessionId = Annotated[str, StringConstraints(pattern=r"^ses_[A-Za-z0-9_-]{8,}$")]
OpaqueTaskId = Annotated[str, StringConstraints(pattern=r"^task_[A-Za-z0-9_-]{8,}$")]
OpaqueArtifactId = Annotated[str, StringConstraints(pattern=r"^(artifact|input|output|result|script)-[A-Za-z0-9_-]+$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class TimestampedContract(ContractModel):
    occurred_at: datetime
