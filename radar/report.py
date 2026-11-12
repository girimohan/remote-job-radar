"""Daily digest in Markdown (also posted as a GitHub issue, which emails you)."""
from __future__ import annotations

ELIG_TEXT = {"europe": "Europe", "finland": "Finland", "worldwide": "Worldwide", "unclear": "Unclear (check)",
             "eu_country": "One European country"}


def digest(payload: dict, dashboard_url: str = "", top: int = 15) -> str:
    jobs = payload["jobs"]
    today = payload["date"]
    new = [j for j in jobs if j["first_seen"] == today] if not payload.get("first_run") else []
    shown = (new or jobs)[:top]
    f = payload["funnel"]
    lines = [f"# Job radar, {today}", ""]
    if payload.get("first_run"):
        lines.append(f"First run: **{len(jobs)} matching jobs** found. Top {len(shown)} below.")
    elif new:
        lines.append(f"**{len(new)} new** matching jobs today ({len(jobs)} open in total). Top {len(shown)} new ones below.")
    else:
        lines.append(f"No new matches today. {len(jobs)} matching jobs are still open; the best {len(shown)} are below.")
    if dashboard_url:
        lines.append(f"\nFull list and application tracker: {dashboard_url}")
    lines += ["", "| Score | Role | Job | Company | Open to | Why |", "|---|---|---|---|---|---|"]
    for j in shown:
        title = j["title"].replace("|", "/")[:80]
        why = "; ".join(j["reasons"][:2]).replace("|", "/")
        lines.append(f"| {j['score']} | {j['role']} | [{title}]({j['url']}) | {j['company'][:30].replace('|', '/')} | "
                     f"{ELIG_TEXT.get(j['eligibility'], j['eligibility'])} | {why} |")
    lines += ["", "## Today's funnel", "",
              f"- {f['collected']:,} postings collected",
              f"- {f['role_mismatch']:,} not one of your roles",
              f"- {f['us_only']:,} US-only, {f['other_region']:,} other regions, {f['not_remote']:,} not remote",
              f"- {f['agency']:,} from agencies, {f['duplicate']:,} duplicates, {f['low_score']:,} below the score cut-off",
              f"- **{f['matched']:,} matches**", "", "## Sources", ""]
    for s in payload["sources"]:
        state = "ok" if s["ok"] else f"FAILED ({s['error']})"
        extra = f" ({s['detail']})" if s["detail"] else ""
        lines.append(f"- {s['name']}: {state}, {s['fetched']:,} fetched, {s['matched']} matched{extra}")
    return "\n".join(lines) + "\n"
