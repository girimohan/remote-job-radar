from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

from . import llm, pipeline, report

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"


def _cfg(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _reader(cfg: dict, cache: Path | None = None) -> llm.Reader:
    lc = cfg.get("llm", {})
    return llm.Reader(model=lc.get("model", llm.DEFAULT_MODEL), max_calls=10_000, workers=lc.get("workers", 4),
                      cache_path=cache or ROOT / "data" / "llm_cache.json")


def cmd_run(args) -> int:
    cfg = _cfg(args.config)
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
    u = payload.get("llm", {})
    if u.get("enabled"):
        print(f"  LLM {u['model']}: {u['calls']} new reads, {u['cached']} cached, {u['failed']} failed, "
              f"{u['unverified']} unverified, {f.get('llm_dropped', 0)} jobs dropped, ${u['cost_usd']:.4f}")
    else:
        print("  LLM: off (set ANTHROPIC_API_KEY to turn it on)")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"new_today={payload['new_today']}\nmatched={f['matched']}\nfirst_run={'true' if payload['first_run'] else 'false'}\n")
    return 0


def cmd_sample(args) -> int:
    from . import evaluate
    cfg = _cfg(args.config)
    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    print("Collecting live postings (a few minutes)...")
    results = pipeline.collect(cfg, companies)
    rows = evaluate.build_sample([j for r in results for j in r.jobs], cfg, n=args.n, seed=args.seed)
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists; labels refer to it. Use --force to replace it.", file=sys.stderr)
        return 1
    evaluate.write_jsonl(out, rows)
    from collections import Counter
    print(f"Wrote {len(rows)} postings to {out}: " + ", ".join(f"{k} {v}" for k, v in Counter(r['rule_label'] for r in rows).items()))
    print("Next: python -m radar label")
    return 0


def cmd_label(args) -> int:
    from . import evaluate
    added = evaluate.label_interactively(Path(args.sample), Path(args.labels))
    total = len(evaluate.read_jsonl(Path(args.labels)))
    print(f"\nAdded {added}. {total} labelled in total. Next: python -m radar eval")
    return 0


def cmd_eval(args) -> int:
    from . import evaluate
    cfg = _cfg(args.config)
    reader = None if args.rules_only else _reader(cfg, ROOT / "eval" / "llm_cache.json")
    if reader is not None and not reader.enabled:
        print("ANTHROPIC_API_KEY is not set: evaluating the rules only.")
        reader = None
    try:
        result = evaluate.evaluate(Path(args.sample), Path(args.labels), reader)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    if reader:
        reader.save()
    text = evaluate.report(result)
    Path(args.out).write_text(text, encoding="utf-8")
    Path(args.out).with_suffix(".json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(text.split("## ", 2)[1] if "## " in text else text)
    print(f"Full report: {args.out}")
    return 0


def _read_pasted() -> str:
    print("Paste the job posting, then type END on its own line and press Enter:")
    lines = []
    for line in sys.stdin:
        if line.strip() == "END":
            break
        lines.append(line)
    return "".join(lines)


def cmd_tailor(args) -> int:
    from . import tailor
    title, company, url = args.title or "", args.company or "", ""
    if args.job_id:
        data = json.loads((ROOT / "docs" / "jobs.json").read_text(encoding="utf-8"))
        hit = next((j for j in data["jobs"] if j["id"] == args.job_id), None)
        if not hit:
            print(f"No job with id {args.job_id} in docs/jobs.json (the id is shown in the dashboard).", file=sys.stderr)
            return 1
        title, company, url = title or hit["title"], company or hit["company"], hit["url"]
        print(f"{title} at {company}\n{url}")
    job_text = Path(args.job).read_text(encoding="utf-8") if args.job else _read_pasted()
    if len(job_text.strip()) < 200:
        print("That posting is too short to tailor against. Paste the full description.", file=sys.stderr)
        return 1
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("Set ANTHROPIC_API_KEY first.", file=sys.stderr)
        return 1
    cfg = _cfg(args.config)
    out = tailor.tailor(tailor.read_document(Path(args.cv)), job_text, job_title=title, company=company, api_key=key,
                        model=args.model or cfg.get("tailor", {}).get("model", tailor.DEFAULT_MODEL),
                        dense=not args.no_dense)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{company} {title}".lower()).strip("-")[:70] or "job"
    dest = ROOT / "tailored" / f"{dt.date.today().isoformat()}-{slug}.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(tailor.render(out, title or "Job", company or "", url), encoding="utf-8")
    g = out["grounding"]
    print(f"Wrote {dest}\nGrounded claims: {g['grounded']} of {g['claims']}. Retrieval: {', '.join(out['retrieval'])}.")
    if g["issues"]:
        print("Check the flagged lines before using them.")
    return 0


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
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("sample", help="pick a balanced sample of live postings to label -> eval/sample.jsonl")
    s.add_argument("--config", default=str(ROOT / "config.yaml"))
    s.add_argument("--companies", default=str(ROOT / "data" / "companies.json"))
    s.add_argument("--n", type=int, default=80)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default=str(EVAL / "sample.jsonl"))
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_sample)

    l = sub.add_parser("label", help="label the sample by hand in the terminal -> eval/labels.jsonl")
    l.add_argument("--sample", default=str(EVAL / "sample.jsonl"))
    l.add_argument("--labels", default=str(EVAL / "labels.jsonl"))
    l.set_defaults(fn=cmd_label)

    e = sub.add_parser("eval", help="score rules and LLM against your labels -> eval/report.md")
    e.add_argument("--config", default=str(ROOT / "config.yaml"))
    e.add_argument("--sample", default=str(EVAL / "sample.jsonl"))
    e.add_argument("--labels", default=str(EVAL / "labels.jsonl"))
    e.add_argument("--out", default=str(EVAL / "report.md"))
    e.add_argument("--rules-only", action="store_true")
    e.set_defaults(fn=cmd_eval)

    t = sub.add_parser("tailor", help="tailor your CV to one job with RAG -> tailored/<job>.md (local only)")
    t.add_argument("--cv", required=True, help="your CV as .md, .txt, .pdf or .docx (keep it in private/)")
    t.add_argument("--job-id", help="job id from the dashboard; you will be asked to paste the posting")
    t.add_argument("--job", help="file with the job posting text")
    t.add_argument("--title")
    t.add_argument("--company")
    t.add_argument("--model")
    t.add_argument("--no-dense", action="store_true", help="BM25 only, skip embeddings")
    t.add_argument("--config", default=str(ROOT / "config.yaml"))
    t.set_defaults(fn=cmd_tailor)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
