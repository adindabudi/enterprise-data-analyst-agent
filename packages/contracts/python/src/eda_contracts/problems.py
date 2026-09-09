from pydantic import Field

from .base import ContractModel


class ApiProblem(ContractModel):
    type: str = Field(default="about:blank", max_length=200)
    title: str = Field(min_length=1, max_length=120)
    status: int = Field(ge=400, le=599)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    correlation_id: str = Field(pattern=r"^corr_[A-Za-z0-9_-]{8,}$")
    detail: str | None = Field(default=None, max_length=500)
