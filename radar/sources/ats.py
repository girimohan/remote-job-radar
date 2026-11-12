"""Company career pages hosted on Greenhouse, Lever and Ashby (public, documented job-board APIs)."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt

from ..http import FetchError, get
from ..models import Job
from ..text import strip_html
from . import SourceResult

WORKERS = 8


def parse_greenhouse(slug: str, data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs") or []:
        out.append(Job(
            source="greenhouse", source_id=f"{slug}:{j.get('id')}",
            company=j.get("company_name") or slug, title=(j.get("title") or "").strip(),
            url=j.get("absolute_url") or "", location=((j.get("location") or {}).get("name") or ""),
            posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
            description=strip_html(j.get("content"))[:20000],
        ))
    return out


def parse_lever(slug: str, data: list) -> list[Job]:
    out = []
    if not isinstance(data, list):   # Lever answers unknown boards with an error object
        return out
    for j in data:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or ([c["location"]] if c.get("location") else [])
        desc = " ".join(filter(None, [
            j.get("descriptionPlain"),
            *[f"{x.get('text', '')} {strip_html(x.get('content'))}" for x in j.get("lists") or []],
            j.get("additionalPlain"),
        ]))
        sr = j.get("salaryRange") or {}
        salary = f"{sr.get('currency', '')} {sr['min']:,.0f}-{sr['max']:,.0f}".strip() if sr.get("min") and sr.get("max") else ""
        created = j.get("createdAt")
        posted = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).date().isoformat() if created else ""
        out.append(Job(
            source="lever", source_id=f"{slug}:{j.get('id')}", company=slug, title=(j.get("text") or "").strip(),
            url=j.get("hostedUrl") or "", location="; ".join(locs),
            remote=True if j.get("workplaceType") == "remote" else None,
            posted=posted, description=desc[:20000], employment_type=c.get("commitment") or "", salary=salary,
        ))
    return out


def parse_ashby(slug: str, data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        parts = [j.get("location"), addr.get("addressCountry")]
        parts += [s.get("location") for s in j.get("secondaryLocations") or []]
        seen, locs = set(), []
        for p in parts:
            if p and p not in seen:
                seen.add(p)
                locs.append(p)
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(Job(
            source="ashby", source_id=f"{slug}:{j.get('id')}", company=slug, title=(j.get("title") or "").strip(),
            url=j.get("jobUrl") or j.get("applyUrl") or "", location="; ".join(locs),
            remote=True if j.get("isRemote") else None, posted=(j.get("publishedAt") or "")[:10],
            description=(j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")))[:20000],
            employment_type=j.get("employmentType") or "", salary=comp,
        ))
    return out


ATS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", parse_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", parse_ashby),
}


def _one(ats: str, slug: str):
    url, parse = ATS[ats]
    try:
        data = get(url.format(slug=slug))
    except FetchError as e:
        return slug, None, str(e)
    if data is None:
        return slug, [], "gone"
    try:
        jobs = parse(slug, data)
    except Exception as e:  # one odd board must not sink the run
        return slug, None, f"parse error: {type(e).__name__}"
    for j in jobs:
        if j.company == slug:
            j.company = slug.replace("-", " ").replace(".", " ").title()
    return slug, jobs, ""


def fetch(ats: str, slugs: list[str], limit: int | None = None) -> SourceResult:
    slugs = slugs[:limit] if limit else slugs
    res = SourceResult(name=ats)
    failed, gone = [], 0
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for slug, jobs, note in ex.map(lambda s: _one(ats, s), slugs):
            if jobs is None:
                failed.append(f"{slug} ({note})")
            else:
                gone += note == "gone"
                res.jobs.extend(jobs)
    answered = len(slugs) - len(failed)
    res.detail = f"{answered} of {len(slugs)} boards answered" + (f", {gone} no longer exist" if gone else "")
    if slugs and len(failed) > len(slugs) / 2:
        res.ok = False
        res.error = "most boards failed, e.g. " + "; ".join(failed[:3])
    elif failed:
        res.detail += f"; failed: {', '.join(failed[:5])}" + ("…" if len(failed) > 5 else "")
    return res
