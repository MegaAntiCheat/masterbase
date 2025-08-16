"""Module of pydantic models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ReportReason(str, Enum):
    """Valid reasons for reports."""

    BOT = "bot"
    CHEATER = "cheater"


class ReportBody(BaseModel):
    """Report model for report post request body."""

    session_id: str
    target_steam_id: int
    reason: ReportReason


class Detection(BaseModel):
    """A single detection from the analysis client."""

    tick: int
    algorithm: str
    player: int
    data: Any


class Analysis(BaseModel):
    """The body of the POST /demos endpoint."""

    author: str
    detections: list[Detection]
    duration: int
    map: str
    server_ip: str


class ExportTable(str, Enum):
    """Tables to be allowed in database exports."""

    DEMOS = "demo_sessions"
    REPORTS = "reports"


class LateBytesBody(BaseModel):
    """Report model for late_bytes post request body."""

    late_bytes: str

class MarkIngestedBody(BaseModel):
    """Model for ingest post request body."""

    session_ids: list[str]
