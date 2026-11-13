"""Collect -> keep remote -> keep eligible -> match role -> score -> dedupe -> merge with yesterday."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from . import eligibility, llm, scoring
from .models import Job
from .sources import SourceResult, ats, boards, hackernews

ELIG_RANK = {"europe": 0, "finland": 1, "worldwide": 2, "unclear": 3, "eu_country": 4}
SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0, "hackernews": 1, "himalayas": 2,
               "remotive": 2, "weworkremotely": 2, "jobicy": 2, "arbeitnow": 2, "workingnomads": 2, "remoteok": 3}
_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]+$|\s*\([^()]+\)\s*$")
_NORM = re.compile(r"[^a-z0-9]+")


def collect(cfg: dict, companies: dict, only: set[str] | None = None, limit_boards: int | None = None) -> list[SourceResult]:
    src = cfg.get("sources", {})

    def on(name: str) -> bool:
        return src.get(name, {}).get("enabled", True) and (only is None or name in only)

    results: list[SourceResult] = []
    for name in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        if on(name):
            results.append(ats.fetch(name, companies.get(name, []), limit_boards))
    if on("himalayas"):
        results.append(boards.fetch_himalayas(max_pages=src.get("himalayas", {}).get("max_pages", 100)))
    if on("remoteok"):
        results.append(boards.fetch_remoteok())
    if on("remotive"):
        results.append(boards.fetch_remotive())
    if on("weworkremotely"):
        results.append(boards.fetch_wwr())
    if on("jobicy"):
        results.append(boards.fetch_jobicy(src.get("jobicy", {}).get("regions", ["europe", "emea", "anywhere"])))
    if on("arbeitnow"):
        results.append(boards.fetch_arbeitnow(max_pages=src.get("arbeitnow", {}).get("max_pages", 5)))
    if on("workingnomads"):
        results.append(boards.fetch_workingnomads())
    if on("hackernews"):
        results.append(hackernews.fetch())
    return results


def _key(job: Job) -> str:
    return _NORM.sub(" ", f"{job.company} {job.title}".lower()).strip()


def process(jobs: list[Job], cfg: dict, today: dt.date) -> tuple[list[Job], dict]:
    """Filter and score. Returns (kept jobs, funnel counts)."""
    funnel = {"collected": len(jobs), "agency": 0, "not_remote": 0, "us_only": 0, "other_region": 0,
              "role_mismatch": 0, "low_score": 0, "duplicate": 0}
    keep_labels = set(cfg.get("keep_eligibility", ELIG_RANK))
    block = [b.lower() for b in cfg.get("agency_blocklist", [])]
    min_score = cfg.get("min_score", 0)

    best: dict[str, Job] = {}
    for job in jobs:
        if not job.title or not job.url:
            continue
        company = (job.company or "").lower()
        if any(b in company for b in block):
            funnel["agency"] += 1
            continue
        # Cheap check first: role matching discards most postings before the regex-heavy steps.
        role = scoring.match_role(job, cfg)
        if not role:
            funnel["role_mismatch"] += 1
            continue
        label, reason = eligibility.classify(job)
        if label not in keep_labels:
            funnel[label if label in funnel else "other_region"] += 1
            continue
        job.role, job.eligibility, job.eligibility_reason = role, label, reason
        job.remote = eligibility.is_remote(job)
        scoring.score(job, cfg, today)
        if job.score < min_score:
            funnel["low_score"] += 1
            continue
        k = _key(job)
        cur = best.get(k)
        rank = (ELIG_RANK.get(job.eligibility, 9), SOURCE_RANK.get(job.source, 9), -job.score)
        if cur is None:
            best[k] = job
        else:
            funnel["duplicate"] += 1
            cur_rank = (ELIG_RANK.get(cur.eligibility, 9), SOURCE_RANK.get(cur.source, 9), -cur.score)
            if rank < cur_rank:
                best[k] = job
    survivors = collapse_variants(list(best.values()), funnel)
    kept = sorted(survivors, key=lambda j: (-j.score, j.company.lower(), j.title.lower()))
    funnel["matched"] = len(kept)
    return kept, funnel


def collapse_variants(jobs: list[Job], funnel: dict, threshold: int = 3) -> list[Job]:
    """One job posted separately for many cities ("Engineer - Austin", "Engineer - Berlin", ...) is one job.

    When a company has `threshold` or more postings that differ only in a trailing "- place" or "(place)",
    keep the one that suits you best and record how many there were. Suffixes that are not places
    ("- Payments", "- Growth") are different jobs and are left alone.
    """
    groups: dict[tuple, list[Job]] = {}
    for j in jobs:
        m = _SUFFIX.search(j.title)
        base = j.title[:m.start()] if m and eligibility.names_place(m.group(0)) else j.title
        groups.setdefault((_NORM.sub(" ", j.company.lower()).strip(), _NORM.sub(" ", base.lower()).strip()), []).append(j)
    out = []
    for members in groups.values():
        if len(members) < threshold:
            out.extend(members)
            continue
        members.sort(key=lambda j: (ELIG_RANK.get(j.eligibility, 9), -j.score))
        top = members[0]
        top.variants = len(members)
        top.reasons.append(f"posted for {len(members)} locations")
        funnel["duplicate"] += len(members) - 1
        out.append(top)
    return out


def llm_stage(kept: list[Job], cfg: dict, reader: "llm.Reader", today: dt.date, funnel: dict) -> list[Job]:
    """Let the LLM re-read every rule-approved job, then re-score and drop the ones it shows are not open to you."""
    keep_labels = set(cfg.get("keep_eligibility", ELIG_RANK))
    min_score = cfg.get("min_score", 0)
    funnel["llm_dropped"] = 0
    out = []
    for job, res in zip(kept, reader.read_all(kept, today.isoformat())):
        if res:
            llm.apply(job, res)
            scoring.score(job, cfg, today)
            if job.eligibility not in keep_labels:
                funnel["llm_dropped"] += 1
                continue
            if job.score < min_score:
                funnel["low_score"] += 1
                continue
        out.append(job)
    out.sort(key=lambda j: (-j.score, j.company.lower(), j.title.lower()))
    funnel["matched"] = len(out)
    return out


def merge_first_seen(kept: list[Job], ledger: dict, today: dt.date, keep_days: int = 180) -> int:
    """Stamp each job with the day it was first seen, using a ledger that survives between runs.

    The ledger (not yesterday's output) is the memory, so a source that fails for a day
    does not make its jobs look new again when it comes back. Returns the number new today.
    """
    first_run = not ledger
    new = 0
    for job in kept:
        k = _key(job)
        if k not in ledger:
            ledger[k] = today.isoformat()
            new += 1
        job.first_seen = ledger[k]
    cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
    live = {_key(j) for j in kept}
    for k in [k for k, d in ledger.items() if d < cutoff and k not in live]:
        del ledger[k]
    return 0 if first_run else new


def run(cfg: dict, companies: dict, out_path: Path, today: dt.date | None = None,
        only: set[str] | None = None, limit_boards: int | None = None, ledger_path: Path | None = None,
        reader: "llm.Reader | None" = None, llm_cache_path: Path | None = None) -> dict:
    today = today or dt.date.today()
    ledger_path = ledger_path or out_path.with_name("seen.json")
    results = collect(cfg, companies, only, limit_boards)
    if results and not any(r.ok for r in results):
        raise RuntimeError("every source failed: " + "; ".join(f"{r.name}: {r.error}" for r in results))
    all_jobs = [j for r in results for j in r.jobs]
    kept, funnel = process(all_jobs, cfg, today)

    llm_info = {"enabled": False}
    lcfg = cfg.get("llm", {})
    if reader is None and lcfg.get("enabled", True):
        reader = llm.Reader(model=lcfg.get("model", llm.DEFAULT_MODEL), max_calls=lcfg.get("max_calls_per_run", 600),
                            workers=lcfg.get("workers", 4), cache_path=llm_cache_path or ledger_path.with_name("llm_cache.json"))
    if reader is not None and reader.enabled:
        kept = llm_stage(kept, cfg, reader, today, funnel)
        reader.save(today)
        read = sum(1 for j in kept if j.llm_status)
        llm_info = {"enabled": True, "model": reader.model, **reader.usage.as_dict(),
                    "not_read_yet": len(kept) - read}

    ledger: dict = {}
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except ValueError:
            ledger = {}
    first_run = not ledger
    new_today = merge_first_seen(kept, ledger, today)

    kept_by_source: dict[str, int] = {}
    for j in kept:
        kept_by_source[j.source] = kept_by_source.get(j.source, 0) + 1
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "new_today": new_today,
        "first_run": first_run,
        "funnel": funnel,
        "llm": llm_info,
        "sources": [{"name": r.name, "ok": r.ok, "fetched": len(r.jobs), "matched": kept_by_source.get(r.name, 0),
                     "detail": r.detail, "error": r.error} for r in results],
        "roles": [r["name"] for r in cfg["roles"]],
        "role_searches": {r["name"]: r.get("search", r["name"]) for r in cfg["roles"]},
        "portals": cfg.get("portal_searches", []),
        "jobs": [j.to_public() for j in kept],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return payload
