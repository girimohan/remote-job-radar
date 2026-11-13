"""Measure how well the rules and the LLM decide where a job can be done from.

    python -m radar sample      # collect live postings and pick a balanced sample  -> eval/sample.jsonl
    python -m radar label       # you label them by hand in the terminal             -> eval/labels.jsonl
    python -m radar eval        # rules vs LLM against your labels                   -> eval/report.md

Your labels are the ground truth. The report gives accuracy per method, precision and recall per label,
confusion matrices, and the number that matters most for you: of the jobs shown as open to you,
how many really are (precision), and of the jobs really open to you, how many are shown (recall).
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

from . import eligibility, llm, scoring
from .models import Job

LABELS = llm.LABELS
OPEN = {"finland", "europe", "worldwide"}            # what the dashboard shows as "open to you"
KEYS = {"1": "finland", "2": "europe", "3": "worldwide", "4": "eu_country", "5": "us_only",
        "6": "other_region", "7": "unclear"}
_HINT = re.compile(r"[^.!?\n]*\b(remote|based|located|locat|reside|resid|authori[sz]|eligib|time ?zone|timezone|"
                   r"citizen|visa|relocat|anywhere|worldwide|europe|emea|united states|\bus\b|usa|country|countries|"
                   r"hours|overlap|cet|utc|gmt)\b[^.!?\n]*[.!?]?", re.I)


# ------------------------------------------------------------------ sample
def job_to_row(job: Job, rule_label: str) -> dict:
    return {"id": job.uid, "source": job.source, "company": job.company, "title": job.title, "url": job.url,
            "location": job.location, "remote": job.remote, "employment_type": job.employment_type,
            "restrictions": job.restrictions, "description": (job.description or "")[:llm.MAX_POSTING_CHARS],
            "rule_label": rule_label}


def row_to_job(row: dict) -> Job:
    return Job(row["source"], row["id"].split(":", 1)[-1], row["company"], row["title"], row["url"],
               location=row.get("location", ""), remote=row.get("remote"), description=row.get("description", ""),
               employment_type=row.get("employment_type", ""), restrictions=row.get("restrictions"),
               match_full_text=row["source"] == "hackernews")


def build_sample(jobs: list[Job], cfg: dict, n: int = 80, seed: int = 7) -> list[dict]:
    """Balanced sample of remote, role-matched postings: roughly equal numbers per rule label.

    Balancing matters because the rare labels (finland, worldwide) are the ones you care about;
    a plain random sample would be mostly us_only.
    """
    rng = random.Random(seed)
    pools: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for job in jobs:
        if not job.title or not job.description or len(job.description) < 200:
            continue
        if not scoring.match_role(job, cfg):
            continue
        label, _ = eligibility.classify(job)
        if label == "not_remote":
            continue
        k = (job.company.lower(), job.title.lower())
        if k in seen:
            continue
        seen.add(k)
        pools[label].append(job_to_row(job, label))
    per = max(1, n // max(1, len(pools)))
    out = []
    for label, rows in pools.items():
        rng.shuffle(rows)
        out.extend(rows[:per])
    rest = [r for rows in pools.values() for r in rows[per:]]
    rng.shuffle(rest)
    out.extend(rest[:max(0, n - len(out))])
    rng.shuffle(out)
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ label
def hints(description: str, limit: int = 6) -> list[str]:
    """Sentences that talk about place, so labelling a long posting takes seconds."""
    out = []
    for m in _HINT.finditer(description or ""):
        s = " ".join(m.group(0).split())
        if 15 < len(s) < 400 and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def label_interactively(sample_path: Path, labels_path: Path, ask=input, say=print) -> int:
    """Terminal labelling. The rule label is hidden on purpose, so it does not bias you. Returns labels added."""
    rows = read_jsonl(sample_path)
    done = {r["id"] for r in read_jsonl(labels_path)}
    todo = [r for r in rows if r["id"] not in done]
    say(f"{len(done)} labelled, {len(todo)} to go. Keys: " +
        ", ".join(f"{k}={v}" for k, v in KEYS.items()) + ", o=open in browser, s=skip, q=quit\n")
    added = 0
    for i, r in enumerate(todo, 1):
        say("=" * 80)
        say(f"[{i}/{len(todo)}] {r['title']}  |  {r['company']}")
        say(f"Location field: {r.get('location') or '(none)'}" +
            (f"   Allowed countries: {', '.join(r['restrictions'][:8])}" if r.get("restrictions") else ""))
        say(f"Link: {r['url']}\n")
        hs = hints(r.get("description", ""))
        say("Sentences about place:\n  - " + "\n  - ".join(hs) if hs else "No sentence mentions place.")
        say(f"\nStart of posting: {r.get('description', '')[:500]}…\n")
        while True:
            k = (ask("label> ") or "").strip().lower()
            if k == "o":
                webbrowser.open(r["url"])
                continue
            if k in ("s", "q") or k in KEYS:
                break
            say("Press 1-7, o, s or q.")
        if k == "q":
            break
        if k == "s":
            continue
        with labels_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": r["id"], "label": KEYS[k], "at": dt.date.today().isoformat()}) + "\n")
        added += 1
    return added


# ------------------------------------------------------------------ metrics
def per_class(gold: list[str], pred: list[str]) -> dict[str, dict]:
    out = {}
    for c in LABELS:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
        out[c] = {"support": tp + fn, "precision": prec, "recall": rec, "f1": f1}
    return out


def open_metrics(gold: list[str], pred: list[str]) -> dict:
    """Binary view: is the job shown as open to someone in Finland (finland/europe/worldwide)?"""
    tp = sum(g in OPEN and p in OPEN for g, p in zip(gold, pred))
    fp = sum(g not in OPEN and p in OPEN for g, p in zip(gold, pred))
    fn = sum(g in OPEN and p not in OPEN for g, p in zip(gold, pred))
    return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "shown_but_closed": fp, "open_but_missed": fn}


def confusion(gold: list[str], pred: list[str]) -> dict[str, Counter]:
    m: dict[str, Counter] = {g: Counter() for g in LABELS}
    for g, p in zip(gold, pred):
        m.setdefault(g, Counter())[p] += 1
    return m


def macro_f1(pc: dict) -> float:
    vals = [v["f1"] for v in pc.values() if v["support"] and v["f1"] is not None]
    return sum(vals) / len(vals) if vals else 0.0


def evaluate(sample_path: Path, labels_path: Path, reader: "llm.Reader | None") -> dict:
    rows = {r["id"]: r for r in read_jsonl(sample_path)}
    labels = [l for l in read_jsonl(labels_path) if l["id"] in rows]
    if not labels:
        raise ValueError("no labels yet: run `python -m radar label` first")
    items = []
    for l in labels:
        job = row_to_job(rows[l["id"]])
        rule, _ = eligibility.classify(job)          # current rules, not the ones used when sampling
        res = reader.read(job) if reader and reader.enabled else None
        llm_raw = res["eligibility"] if res else None
        verified = bool(res and res.get("verified"))
        system = llm_raw if verified else rule       # what the radar actually does
        items.append({"id": l["id"], "title": job.title, "company": job.company, "url": job.url,
                      "gold": l["label"], "rules": rule, "llm": llm_raw, "verified": verified,
                      "system": system, "evidence": (res or {}).get("evidence", "")})
    gold = [i["gold"] for i in items]
    methods = {"rules": [i["rules"] for i in items]}
    if any(i["llm"] for i in items):
        methods["llm"] = [i["llm"] or "unclear" for i in items]
        methods["system"] = [i["system"] for i in items]
    result = {"n": len(items), "date": dt.date.today().isoformat(), "gold_counts": dict(Counter(gold)),
              "methods": {}, "items": items}
    for name, pred in methods.items():
        pc = per_class(gold, pred)
        result["methods"][name] = {
            "accuracy": sum(g == p for g, p in zip(gold, pred)) / len(gold), "macro_f1": macro_f1(pc),
            "open": open_metrics(gold, pred), "per_class": pc,
            "confusion": {g: dict(c) for g, c in confusion(gold, pred).items()},
        }
    if reader and reader.enabled:
        result["llm_usage"] = {"model": reader.model, **reader.usage.as_dict(),
                               "evidence_verified": sum(i["verified"] for i in items if i["llm"]),
                               "answered": sum(1 for i in items if i["llm"])}
    return result


# ------------------------------------------------------------------ report
def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


NAMES = {"rules": "Rules only", "llm": "LLM only", "system": "Radar (LLM when its evidence checks out, else rules)"}


def report(result: dict) -> str:
    m = result["methods"]
    lines = [f"# Eligibility evaluation, {result['date']}", "",
             f"{result['n']} postings labelled by hand. Labels: " +
             ", ".join(f"{k} {v}" for k, v in sorted(result["gold_counts"].items(), key=lambda kv: -kv[1])) + ".", "",
             "## Headline", "",
             "| Method | Accuracy | Macro F1 | Shown as open: really open (precision) | Really open: shown (recall) |",
             "|---|---|---|---|---|"]
    for k, v in m.items():
        lines.append(f"| {NAMES[k]} | {_pct(v['accuracy'])} | {v['macro_f1']:.2f} | {_pct(v['open']['precision'])} "
                     f"| {_pct(v['open']['recall'])} |")
    if "llm_usage" in result:
        u = result["llm_usage"]
        lines += ["", f"LLM: {u['model']}, {u['answered']} answers, {u['evidence_verified']} with evidence found "
                      f"verbatim in the posting, {u['calls']} new API calls, ${u['cost_usd']:.4f}."]
    for k, v in m.items():
        lines += ["", f"## {NAMES[k]}: per label", "", "| Label | Support | Precision | Recall | F1 |", "|---|---|---|---|---|"]
        for c, s in v["per_class"].items():
            if s["support"] or s["precision"] is not None:
                f1 = "n/a" if s["f1"] is None else f"{s['f1']:.2f}"
                lines.append(f"| {c} | {s['support']} | {_pct(s['precision'])} | {_pct(s['recall'])} | {f1} |")
        present = [c for c in LABELS if any(v["confusion"].get(c, {}).values()) or
                   any(v["confusion"].get(g, {}).get(c) for g in LABELS)]
        lines += ["", "Confusion matrix (rows: your label, columns: predicted)", "",
                  "| | " + " | ".join(present) + " |", "|---" * (len(present) + 1) + "|"]
        for g in present:
            row = v["confusion"].get(g, {})
            lines.append(f"| **{g}** | " + " | ".join(str(row.get(p, 0) or "") for p in present) + " |")
    if "system" in m:
        wrong = [i for i in result["items"] if i["system"] != i["gold"]]
        lines += ["", f"## Where the radar is still wrong ({len(wrong)})", ""]
        for i in wrong[:25]:
            ev = f' Evidence: "{i["evidence"][:140]}"' if i["evidence"] else ""
            lines.append(f"- [{i['title'][:70]}]({i['url']}) ({i['company']}): yours **{i['gold']}**, rules "
                         f"{i['rules']}, LLM {i['llm'] or 'no answer'}{'' if i['verified'] else ' (unverified)'}.{ev}")
        fixed = [i for i in result["items"] if i["rules"] != i["gold"] and i["system"] == i["gold"]]
        broke = [i for i in result["items"] if i["rules"] == i["gold"] and i["system"] != i["gold"]]
        lines += ["", f"The LLM corrected the rules on {len(fixed)} postings and made {len(broke)} correct rule "
                      f"labels wrong."]
    return "\n".join(lines) + "\n"
