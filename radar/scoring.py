"""Match a job to a target role and give it a 0-100 score with reasons."""
from __future__ import annotations

import datetime as dt
import re

from .models import Job
from .text import first_keyword, has_keyword

ELIGIBILITY_POINTS = {"europe": 15, "finland": 12, "worldwide": 12, "unclear": 5, "eu_country": 2}
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)
CONTRACT = re.compile(r"\b(contract(or)?|freelance|part[- ]time|hourly|1099|b2b)\b", re.I)


def match_role(job: Job, cfg: dict) -> str | None:
    title = (job.title or "").lower()
    haystack = title
    if job.match_full_text:
        # Free-text posts (Hacker News) often list several roles, so read the whole post
        # and do not let one excluded word in the header drop it.
        haystack = f"{title} {(job.description or '').lower()}"
    elif first_keyword(title, cfg.get("exclude_title", [])):
        return None
    for role in cfg["roles"]:
        if first_keyword(haystack, role["title_any"]):
            return role["name"]
    return None


def level_of(title: str, cfg: dict) -> str:
    t = (title or "").lower()
    levels = cfg.get("levels", {})
    for name in ("staff", "junior", "senior"):
        if first_keyword(t, levels.get(name, [])):
            return name
    return "mid"


def years_required(desc: str) -> int | None:
    vals = [int(m.group(1)) for m in YEARS.finditer(desc or "") if 0 < int(m.group(1)) <= 15]
    return max(vals) if vals else None


def score(job: Job, cfg: dict, today: dt.date | None = None) -> None:
    """Fill job.level, job.score and job.reasons. job.role and job.eligibility must be set."""
    today = today or dt.date.today()
    weights = {r["name"]: float(r.get("weight", 1)) for r in cfg["roles"]}
    reasons: list[str] = []
    total = 40 * weights.get(job.role, 0.5)

    text = f"{job.title} {job.description}".lower()
    skills = [s for s in cfg.get("profile", {}).get("skills", []) if has_keyword(text, s)]
    total += min(len(skills), 8) / 8 * 25
    if skills:
        reasons.append("skills: " + ", ".join(skills[:6]))

    job.level = level_of(job.title, cfg)
    total += {"junior": 15, "mid": 8, "senior": 0, "staff": -10}[job.level]
    if job.level == "junior":
        reasons.append("junior-friendly title")
    elif job.level in ("senior", "staff"):
        reasons.append(f"{job.level}-level title")

    yrs = years_required(job.description)
    if yrs and yrs >= 5:
        total -= 8
        reasons.append(f"asks for {yrs}+ years")
    elif yrs:
        reasons.append(f"asks for {yrs}+ years")

    total += ELIGIBILITY_POINTS.get(job.eligibility, 0)

    if CONTRACT.search(f"{job.employment_type} {job.title} {job.description[:1500]}"):
        total += 5
        reasons.append("contract or part-time possible")

    if job.posted:
        try:
            age = (today - dt.date.fromisoformat(job.posted[:10])).days
            if age <= 7:
                total += 5
                reasons.append("posted this week")
            elif age <= 30:
                total += 2
        except ValueError:
            pass

    job.score = max(0, min(100, round(total)))
    job.reasons = reasons
