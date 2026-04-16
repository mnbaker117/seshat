"""
Sink contract and result type.

A sink is a destination for processed book files. Each sink implements
`deliver()` which takes a file path and metadata, and returns a
`SinkResult` describing what happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from app.metadata.extract import BookMetadata


@dataclass(frozen=True)
class SinkResult:
    """Outcome of delivering a file to a sink."""

    success: bool
    sink_name: str
    detail: str = ""
    error: Optional[str] = None


class Sink(Protocol):
    """Contract every sink implements."""

    name: str

    async def deliver(
        self,
        file_path: str,
        metadata: BookMetadata,
    ) -> SinkResult: ...
