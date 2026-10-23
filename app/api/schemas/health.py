"""Public liveness response without settings or document data."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Confirm that the API process can answer a request."""

    status: Literal["ok"]
    service: str
