import json
from pathlib import Path

import pytest
import yaml

from radar import evaluate, llm, tailor
from radar.models import Job

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
LONG = " Python, FastAPI and LLM tooling every day in a small product team." * 5


def job(i, title, location, desc=""):
    return Job("ashby", f"acme:{i}", f"Co{i}", title, f"https://e/{i}", location=location, remote=True,
               description=(desc + LONG))


# ---------------------------------------------------------------- sample + label
def test_sample_is_balanced_and_skips_non_matches():
    jobs = ([job(i, "Backend Engineer", "Remote - US") for i in range(30)]
            + [job(100 + i, "AI Engineer", "Remote - Europe") for i in range(5)]
            + [job(200 + i, "Backend Engineer", "Helsinki, Finland") for i in range(5)]
            + [job(300, "Account Executive", "Remote - Europe"), job(301, "Backend Engineer", "Berlin")])
    jobs[-1].remote = None                                                   # office job
    rows = evaluate.build_sample(jobs, CFG, n=12)
    labels = [r["rule_label"] for r in rows]
    assert len(rows) == 12 and labels.count("europe") == 4 and labels.count("finland") == 4
    assert not any("Account" in r["title"] for r in rows)
    assert evaluate.row_to_job(rows[0]).description.startswith(rows[0]["description"][:20])


def test_labelling_writes_labels_and_resumes(tmp_path):
    rows = [evaluate.job_to_row(job(i, "Backend Engineer", "Remote", "You must be located in the EU."), "europe")
            for i in range(3)]
    sample, labels = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    evaluate.write_jsonl(sample, rows)
    keys = iter(["2", "x", "s", "q"])
    out = []
    assert evaluate.label_interactively(sample, labels, ask=lambda _: next(keys), say=out.append) == 1
    assert evaluate.read_jsonl(labels)[0] == {"id": rows[0]["id"], "label": "europe", "at": evaluate.read_jsonl(labels)[0]["at"]}
    assert any("must be located in the EU" in line for line in out)          # place sentences are surfaced
    assert not any("europe" == line.strip() for line in out)                 # rule label never shown
    keys = iter(["5", "5"])
    evaluate.label_interactively(sample, labels, ask=lambda _: next(keys), say=lambda *_: None)
    assert [l["label"] for l in evaluate.read_jsonl(labels)] == ["europe", "us_only", "us_only"]


# ---------------------------------------------------------------- metrics
def test_metrics():
    gold = ["europe", "europe", "us_only", "finland", "unclear"]
    pred = ["europe", "us_only", "us_only", "worldwide", "europe"]
    pc = evaluate.per_class(gold, pred)
    assert pc["europe"]["precision"] == 0.5 and pc["europe"]["recall"] == 0.5
    assert pc["us_only"]["precision"] == 0.5 and pc["us_only"]["recall"] == 1.0
    om = evaluate.open_metrics(gold, pred)
    # shown as open: europe, worldwide, europe(unclear) -> 2 of 3 really open; really open: 3 -> 2 shown
    assert om["precision"] == pytest.approx(2 / 3) and om["recall"] == pytest.approx(2 / 3)
    assert evaluate.confusion(gold, pred)["europe"] == {"europe": 1, "us_only": 1}


def test_evaluate_rules_vs_llm(tmp_path):
    hidden = "This role requires residence in one of our approved states and a valid SSN."
    rows = [evaluate.job_to_row(job(1, "AI Engineer", "Remote", hidden), "unclear"),
            evaluate.job_to_row(job(2, "AI Engineer", "Remote - Europe"), "europe"),
            evaluate.job_to_row(job(3, "AI Engineer", "Remote", "We hire anywhere in the world."), "worldwide")]
    sample, labels = tmp_path / "s.jsonl", tmp_path / "l.jsonl"
    evaluate.write_jsonl(sample, rows)
    evaluate.write_jsonl(labels, [{"id": rows[0]["id"], "label": "us_only"}, {"id": rows[1]["id"], "label": "europe"},
                                  {"id": rows[2]["id"], "label": "worldwide"}])

    def call(system, user, schema):
        if "SSN" in user:
            return {"eligibility": "us_only", "evidence": "requires residence in one of our approved states",
                    "employment": "employee", "languages_required": [], "min_years": -1, "summary": "x"}, {}
        if "Remote - Europe" in user:
            return {"eligibility": "worldwide", "evidence": "invented sentence", "employment": "employee",
                    "languages_required": [], "min_years": -1, "summary": "x"}, {}
        return {"eligibility": "worldwide", "evidence": "We hire anywhere in the world.", "employment": "employee",
                "languages_required": [], "min_years": -1, "summary": "x"}, {}

    res = evaluate.evaluate(sample, labels, llm.Reader(call=call))
    m = res["methods"]
    assert m["rules"]["accuracy"] == pytest.approx(2 / 3)          # rules miss the hidden US restriction
    assert m["llm"]["accuracy"] == pytest.approx(2 / 3)            # raw LLM gets #1, but #2 is wrong
    assert m["system"]["accuracy"] == 1.0                          # #2's evidence is invented, so rules win there
    text = evaluate.report(res)
    assert "Rules only" in text and "The LLM corrected the rules on 1 postings and made 0" in text
    with pytest.raises(ValueError):
        evaluate.evaluate(sample, tmp_path / "none.jsonl", None)


# ---------------------------------------------------------------- RAG tailoring
CV = """# Alex Example
Helsinki, Finland. MSc student in machine learning.

## Experience
- Built a retrieval-augmented assistant with LlamaIndex and pgvector that answers questions over 1,200 policy documents.
- Shipped FastAPI services on Docker and Supabase for a small bookkeeping product.
- Set up an MLOps pipeline with GitHub Actions that retrains a forecasting model nightly.

## Projects
- Hackathon finalist: built an LLM agent that triages support tickets in 48 hours.

## Skills
Python, PyTorch, LangChain, PostgreSQL, React, Next.js.
"""
POSTING = """AI Engineer, remote in Europe.
You will build RAG pipelines and LLM agents in Python.
You have experience deploying APIs with FastAPI and Docker.
Experience with vector databases such as pgvector is required.
Nice to have: Kubernetes in production.
We offer a friendly team and flexible hours."""


def test_chunking_keeps_sections_and_bullets():
    chunks = tailor.chunk_cv(CV)
    texts = [c.text for c in chunks]
    assert any(t.startswith("Built a retrieval-augmented assistant") for t in texts)
    rag = next(c for c in chunks if "pgvector" in c.text)
    assert rag.section == "Experience" and rag.id.startswith("C")
    long = tailor.chunk_cv("## A\n" + "This is a sentence about Python. " * 40)
    assert len(long) > 2 and all(len(c.text) <= 420 for c in long)


def test_requirements_and_bm25_retrieval():
    reqs = tailor.requirements(POSTING)
    assert any("pgvector" in r for r in reqs) and not any("friendly team" in r for r in reqs)
    chunks = tailor.chunk_cv(CV)
    r = tailor.Retriever(chunks, dense=False)
    assert r.methods == ["bm25"]
    top = r.search("vector databases such as pgvector", 1)[0][0]
    assert "pgvector" in top.text
    assert r.search("Kubernetes in production", 3) == []                    # nothing in the CV: no fake support
    used, support = tailor.retrieve(chunks, reqs, r)
    assert any("FastAPI" in c.text for c in used) and all(ids for req, ids in support.items() if "pgvector" in req)


def test_grounding_flags_unsupported_numbers_and_citations():
    chunks = tailor.chunk_cv(CV)
    used = [c for c in chunks if "pgvector" in c.text or "FastAPI" in c.text]
    rag, api = (next(c.id for c in used if k in c.text) for k in ("pgvector", "FastAPI"))
    result = {
        "summary": [{"text": "Built RAG over 1,200 documents.", "sources": [rag]},
                    {"text": "Built RAG over 5,000 documents.", "sources": [rag]},
                    {"text": "Led a team of engineers.", "sources": []}],
        "bullets": [{"requirement": "APIs", "text": "Shipped FastAPI services.", "sources": [api, "C99"]}],
        "cover_note": [{"text": "I live in Finland.", "sources": [api]}],
        "gaps": ["Kubernetes in production"],
    }
    g = tailor.grounding(result, used)
    assert g["claims"] == 5 and g["grounded"] == 2
    problems = " ".join(p for i in g["issues"] for p in i["problems"])
    assert "5.000" in problems and "cites no CV excerpt" in problems and "C99" in problems


def test_tailor_end_to_end_with_fake_model():
    seen = {}

    def call(system, user, schema):
        seen["user"] = user
        ids = {m.group(1): m.group(2) for m in __import__("re").finditer(r"\[(C\d+)\] \(\w+\) (.+)", user)}
        rag = next(i for i, t in ids.items() if "pgvector" in t)
        return {"summary": [{"text": "I build RAG systems with LlamaIndex and pgvector.", "sources": [rag]}],
                "bullets": [], "cover_note": [], "gaps": ["Kubernetes in production"]}, {"input_tokens": 1, "output_tokens": 1}

    out = tailor.tailor(CV, POSTING, job_title="AI Engineer", company="Acme", call=call, dense=False)
    assert "CV EXCERPTS" in seen["user"] and "pgvector" in seen["user"]
    assert out["grounding"] == {"claims": 1, "grounded": 1, "issues": []}
    md = tailor.render(out, "AI Engineer", "Acme", "https://e/1")
    assert "Grounding: **1 of 1**" in md and "- Kubernetes in production" in md and "## CV excerpts" in md
