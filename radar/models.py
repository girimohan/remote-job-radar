from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Job:
    """One posting, normalised across every source."""

    source: str            # greenhouse, lever, ashby, himalayas, remoteok, remotive, weworkremotely, hackernews
    source_id: str         # id unique within the source
    company: str
    title: str
    url: str
    location: str = ""                 # raw location text from the source
    remote: bool | None = None         # source's own remote flag; None when it has none
    posted: str = ""                   # ISO date, may be empty
    description: str = ""              # plain text
    employment_type: str = ""
    salary: str = ""
    restrictions: list[str] | None = None   # explicit country list (Himalayas); [] means none
    match_full_text: bool = False      # match roles on title + description (HN posts have no clean title)

    # filled in by the pipeline
    eligibility: str = ""
    eligibility_reason: str = ""
    role: str = ""
    level: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    first_seen: str = ""

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_public(self) -> dict:
        d = asdict(self)
        d["id"] = self.uid
        d["snippet"] = self.description[:420]
        for k in ("description", "restrictions", "match_full_text", "source_id"):
            d.pop(k)
        return d
