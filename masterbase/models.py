"""Module of pydantic models."""

from pydantic import BaseModel


class ReportBody(BaseModel):
    """Report model for report post request body."""

    session_id: str
    target_steam_id: int


class LateBytesBody(BaseModel):
    """Report model for late_bytes post request body."""

    late_bytes: str
