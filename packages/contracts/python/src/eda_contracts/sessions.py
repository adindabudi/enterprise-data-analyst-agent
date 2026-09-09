from datetime import datetime

from pydantic import Field

from .base import ContractModel, OpaqueSessionId


class SessionSummary(ContractModel):
    session_id: OpaqueSessionId
    title: str = Field(min_length=1, max_length=120)
    last_activity_at: datetime
