"""The monthly "Ask HN: Who is hiring?" thread, read through the public Algolia HN API.

Small startups post here directly and often hire contractors abroad without paperwork.
"""
from __future__ import annotations

import html
import re

from ..http import FetchError, get
from ..models import Job
from ..text import strip_html
from . import SourceResult

SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"
_PARA = re.compile(r"<p>|<br\s*/?>|\n", re.I)
_REMOTE = re.compile(r"\bremote\b", re.I)
_NO_REMOTE = re.compile(r"\b(no remote|not remote|on[- ]?site only)\b", re.I)
_ONSITE = re.compile(r"\bon[- ]?site\b", re.I)


def parse_thread(thread: dict) -> list[Job]:
    out = []
    for c in thread.get("children") or []:
        raw = c.get("text") or ""
        if not raw or not c.get("author"):
            continue  # deleted
        first, *rest = [p for p in _PARA.split(html.unescape(raw)) if p.strip()] or [""]
        header = strip_html(first)
        body = strip_html(" ".join(rest))
        whole = f"{header} {body}"
        if _NO_REMOTE.search(whole):
            continue
        # Most posts state REMOTE in the header line. A body-only mention counts unless the header says onsite.
        if not _REMOTE.search(header) and (not _REMOTE.search(body) or _ONSITE.search(header)):
            continue
        company = re.split(r"\s*[|•·]\s*|\s+[-–—]\s+", header, maxsplit=1)[0].strip()[:60]
        out.append(Job(
            source="hackernews", source_id=str(c.get("id")), company=company or "HN post",
            title=header[:160], url=f"https://news.ycombinator.com/item?id={c.get('id')}",
            location=header, remote=True, posted=(c.get("created_at") or "")[:10],
            description=body[:20000], match_full_text=True,
        ))
    return out


def fetch() -> SourceResult:
    res = SourceResult(name="hackernews")
    try:
        hits = (get(SEARCH) or {}).get("hits") or []
        story = next((h for h in hits if (h.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
        if not story:
            res.ok, res.error = False, "no 'Who is hiring?' thread found"
            return res
        thread = get(ITEM.format(id=story["objectID"]), timeout=(6, 60)) or {}
        res.jobs = parse_thread(thread)
        res.detail = story.get("title", "")
    except FetchError as e:
        res.ok, res.error = False, str(e)
    return res
