"""Module of pydantic models."""

from enum import Enum

from pydantic import BaseModel


class ReportReason(str, Enum):
    """Valid reasons for reports."""

    bot = "bot"
    cheater = "cheater"


class ReportBody(BaseModel):
    """Report model for report post request body."""

    session_id: str
    target_steam_id: int
    reason: ReportReason


class LateBytesBody(BaseModel):
    """Report model for late_bytes post request body."""

    late_bytes: str
