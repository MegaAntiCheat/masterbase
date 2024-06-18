"""Module of pydantic models."""

from enum import Enum

from pydantic import BaseModel, Field


class ReportReason(str, Enum):
    """Valid reasons for reports."""

    BOT = "bot"
    CHEATER = "cheater"


class ReportBody(BaseModel):
    """Report model for report post request body."""

    session_id: str
    target_steam_id: int
    reason: ReportReason


class ExportTable(str, Enum):
    """Tables to be allowed in database exports."""

    DEMOS = "demo_sessions"
    REPORTS = "reports"


class LateBytesBody(BaseModel):
    """Report model for late_bytes post request body."""

    late_bytes: str
