"""Remote-only job boards with free public feeds: Himalayas, Remote OK, Remotive, We Work Remotely."""
from __future__ import annotations

import datetime as dt
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from ..http import FetchError, get
from ..models import Job
from ..text import strip_html
from . import SourceResult


def _epoch_date(v) -> str:
    try:
        v = float(v)
        if v > 1e12:
            v /= 1000
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return str(v or "")[:10]


def _money(lo, hi, cur="") -> str:
    try:
        lo, hi = float(lo or 0), float(hi or 0)
    except (TypeError, ValueError):
        return ""
    if lo <= 0 and hi <= 0:
        return ""
    rng = f"{lo:,.0f}-{hi:,.0f}" if lo and hi else f"{max(lo, hi):,.0f}"
    return f"{cur} {rng}".strip()


# ---------------------------------------------------------------- Himalayas
def parse_himalayas(data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs") or []:
        restr = j.get("locationRestrictions")
        restr = [str(x) for x in restr] if isinstance(restr, list) else []
        emp = j.get("employmentType") or ""
        out.append(Job(
            source="himalayas", source_id=str(j.get("guid") or j.get("applicationLink") or j.get("title")),
            company=j.get("companyName") or "", title=(j.get("title") or "").strip(),
            url=j.get("applicationLink") or j.get("guid") or "",
            location=", ".join(restr) if restr else "Worldwide", remote=True,
            posted=_epoch_date(j.get("pubDate")),
            description=strip_html(j.get("description") or j.get("excerpt"))[:20000],
            employment_type=emp if isinstance(emp, str) else ", ".join(emp),
            salary=_money(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or ""),
            restrictions=restr,
        ))
    return out


def fetch_himalayas(max_pages: int = 100) -> SourceResult:
    """Newest listings first, 20 per page (the API's cap), following its cursor."""
    res = SourceResult(name="himalayas")
    url = "https://himalayas.app/jobs/api?limit=20"
    pages = 0
    try:
        for _ in range(max_pages):
            data = get(url) or {}
            batch = parse_himalayas(data)
            if not batch:
                break
            res.jobs.extend(batch)
            pages += 1
            cursor = data.get("nextCursor")
            if not cursor:
                break
            url = f"https://himalayas.app/jobs/api?limit=20&cursor={quote(str(cursor))}"
            time.sleep(0.3)
    except FetchError as e:
        if not res.jobs:
            res.ok, res.error = False, str(e)
    res.detail = f"newest {len(res.jobs):,} listings ({pages} pages)"
    return res


# ---------------------------------------------------------------- Remote OK
def parse_remoteok(data: list) -> list[Job]:
    out = []
    for j in data or []:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is a legal notice
        out.append(Job(
            source="remoteok", source_id=str(j.get("id") or j.get("slug")), company=j.get("company") or "",
            title=(j.get("position") or "").strip(), url=j.get("url") or j.get("apply_url") or "",
            location=j.get("location") or "", remote=True, posted=(j.get("date") or "")[:10],
            description=(strip_html(j.get("description")) + " " + " ".join(j.get("tags") or []))[:20000],
            salary=_money(j.get("salary_min"), j.get("salary_max"), "USD"),
        ))
    return out


def fetch_remoteok() -> SourceResult:
    res = SourceResult(name="remoteok")
    try:
        res.jobs = parse_remoteok(get("https://remoteok.com/api") or [])
    except FetchError as e:
        res.ok, res.error = False, str(e)
    return res


# ---------------------------------------------------------------- Remotive
def parse_remotive(data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs") or []:
        out.append(Job(
            source="remotive", source_id=str(j.get("id")), company=j.get("company_name") or "",
            title=(j.get("title") or "").strip(), url=j.get("url") or "",
            location=j.get("candidate_required_location") or "", remote=True,
            posted=(j.get("publication_date") or "")[:10], description=strip_html(j.get("description"))[:20000],
            employment_type=(j.get("job_type") or "").replace("_", " "), salary=j.get("salary") or "",
        ))
    return out


def fetch_remotive() -> SourceResult:
    res = SourceResult(name="remotive")
    try:
        res.jobs = parse_remotive(get("https://remotive.com/api/remote-jobs") or {})
    except FetchError as e:
        res.ok, res.error = False, str(e)
    return res


# ---------------------------------------------------------------- We Work Remotely
def parse_wwr(xml_text: str) -> list[Job]:
    out = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        f = {c.tag.split("}")[-1]: (c.text or "") for c in item}
        raw = f.get("title", "")
        company, _, title = raw.partition(":")
        if not title:
            company, title = "", raw
        posted = ""
        try:
            posted = parsedate_to_datetime(f.get("pubDate", "")).date().isoformat()
        except (TypeError, ValueError):
            pass
        link = f.get("link") or f.get("guid") or ""
        out.append(Job(
            source="weworkremotely", source_id=link, company=company.strip(), title=title.strip(), url=link,
            location=f.get("region", ""), remote=True, posted=posted,
            description=strip_html(f.get("description"))[:20000], employment_type=f.get("type", ""),
        ))
    return out


def fetch_wwr() -> SourceResult:
    res = SourceResult(name="weworkremotely")
    try:
        text = get("https://weworkremotely.com/remote-jobs.rss", as_json=False)
        res.jobs = parse_wwr(text or "<rss/>")
    except (FetchError, ET.ParseError) as e:
        res.ok, res.error = False, str(e) or type(e).__name__
    return res
