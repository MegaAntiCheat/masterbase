"""Module of pydantic models."""

from typing import Any

from pydantic import BaseModel


class ReportBody(BaseModel):
    """Report model for report post request body."""

    session_id: str
    target_steam_id: int


class LateBytesBody(BaseModel):
    """Report model for late_bytes post request body."""

    late_bytes: str

    def model_post_init(self, __context: Any) -> None:
        """Convert late_bytes to bytes."""
        self.converted_late_bytes = bytes.fromhex(self.late_bytes)
