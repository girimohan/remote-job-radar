"""One module per source. Each exposes fetch(cfg, companies) -> SourceResult."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Job


@dataclass
class SourceResult:
    name: str
    jobs: list[Job] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    detail: str = ""      # e.g. "540 of 571 boards answered"
