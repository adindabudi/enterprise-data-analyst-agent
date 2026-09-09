from enum import StrEnum

from pydantic import Field

from .base import ContractModel


class CommandKind(StrEnum):
    STEER = "steer"
    CANCEL = "cancel"
    AUTH_RESUMED = "auth_resumed"


class SteeringRequest(ContractModel):
    instruction: str = Field(min_length=1, max_length=4000)
