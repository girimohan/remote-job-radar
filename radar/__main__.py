from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import yaml

from . import pipeline, report

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description="Find remote jobs you can take from Finland.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, filter, score and write docs/jobs.json + digests/latest.md")
    r.add_argument("--config", default=str(ROOT / "config.yaml"))
    r.add_argument("--companies", default=str(ROOT / "data" / "companies.json"))
    r.add_argument("--out", default=str(ROOT / "docs" / "jobs.json"))
    r.add_argument("--digest", default=str(ROOT / "digests" / "latest.md"))
    r.add_argument("--sources", help="comma-separated subset, e.g. ashby,hackernews")
    r.add_argument("--limit-boards", type=int, help="only the first N company boards per platform (quick test)")
    r.add_argument("--dashboard-url", default=os.environ.get("DASHBOARD_URL", ""))
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    only = set(args.sources.split(",")) if args.sources else None
    try:
        payload = pipeline.run(cfg, companies, Path(args.out), dt.date.today(), only, args.limit_boards,
                               ledger_path=ROOT / "data" / "seen.json")
    except RuntimeError as e:
        print(e, file=sys.stderr)  # nothing is overwritten when every source is down
        return 1

    text = report.digest(payload, args.dashboard_url)
    dpath = Path(args.digest)
    dpath.parent.mkdir(parents=True, exist_ok=True)
    dpath.write_text(text, encoding="utf-8")

    f = payload["funnel"]
    print(f"collected {f['collected']:,} -> matched {f['matched']:,} (new today: {payload['new_today']})")
    for s in payload["sources"]:
        print(f"  {s['name']:15} {'ok ' if s['ok'] else 'ERR'} fetched {s['fetched']:>6,} matched {s['matched']:>4}  {s['error'] or s['detail']}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"new_today={payload['new_today']}\nmatched={f['matched']}\nfirst_run={'true' if payload['first_run'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
