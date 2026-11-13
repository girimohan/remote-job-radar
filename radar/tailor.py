"""Tailor your CV to one job with retrieval-augmented generation, and check that the result is grounded.

    python -m radar tailor --cv private/cv.md --job-id "ashby:acme:123"      # then paste the posting
    python -m radar tailor --cv private/cv.pdf --job private/job.txt

Steps:
1. Chunk the CV (one chunk per bullet or short paragraph, each tagged with its section heading).
2. Split the posting into requirement sentences.
3. Retrieve the CV chunks that best support each requirement: BM25 keyword search, fused with dense
   embeddings (fastembed, BAAI/bge-small-en-v1.5) when that package is installed (reciprocal rank fusion).
4. Ask Claude to write a summary, bullets and a cover note using only those chunks, citing chunk ids.
5. Grounding check: every claim must cite retrieved chunks, and every number in a claim must appear in the
   chunks it cites. Anything that fails is flagged in the output rather than silently kept.

Runs on your computer. The CV goes only to the Anthropic API, never to the repository.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import llm

DEFAULT_MODEL = "claude-sonnet-5-5"
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]")
_STOP = set("""a an and are as at be by for from has have in is it its of on or our that the their this to was
we were will with you your they them who what which while about into over per via within without can able
using use used work working experience years year strong good great plus etc including""".split())
_NUM = re.compile(r"\d+(?:[.,]\d+)?\s?%?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•▪◦–]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+.+|[A-Z][A-Z &/\-]{3,40}:?)\s*$")
_CUE = re.compile(r"\b(experience|knowledge|proficien|familiar|skill|ability|able to|you have|you will|you'll|"
                  r"must|should|required|requirement|preferred|bonus|nice to have|plus|degree|background|"
                  r"strong|hands-on|expert|build|design|develop|deploy|own|maintain)\b", re.I)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


# ------------------------------------------------------------------ chunking
@dataclass
class Chunk:
    id: str
    section: str
    text: str

    @property
    def full(self) -> str:
        return f"{self.section}: {self.text}" if self.section else self.text


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise SystemExit("Reading a PDF needs pypdf: pip install -r requirements-ai.txt") from e
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        try:
            import docx
        except ImportError as e:
            raise SystemExit("Reading a .docx needs python-docx: pip install -r requirements-ai.txt") from e
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8")


def chunk_cv(text: str, max_chars: int = 420) -> list[Chunk]:
    """One chunk per bullet or paragraph, long ones split at sentence ends, each labelled with its heading."""
    chunks: list[Chunk] = []
    section, para = "", []

    def flush():
        if para:
            add(" ".join(para))
            para.clear()

    def add(text: str):
        text = " ".join(text.split())
        if len(text) < 12:
            return
        while len(text) > max_chars:
            cut = text.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            chunks.append(Chunk(f"C{len(chunks) + 1}", section, text[:cut].strip()))
            text = text[cut:].strip()
        if text:
            chunks.append(Chunk(f"C{len(chunks) + 1}", section, text))

    for line in (text or "").splitlines():
        if not line.strip():
            flush()
            continue
        if _HEADING.match(line) and not _BULLET.match(line):
            flush()
            section = line.strip().lstrip("#").strip().rstrip(":").title()[:60]
            continue
        if _BULLET.match(line):
            flush()
            add(_BULLET.sub("", line))
            continue
        para.append(line.strip())
    flush()
    return chunks


def requirements(job_text: str, limit: int = 12) -> list[str]:
    """Sentences and bullets of the posting that state what the job needs, in order of appearance."""
    parts = []
    for line in (job_text or "").splitlines():
        line = _BULLET.sub("", line).strip()
        parts.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    out = []
    for s in parts:
        if 25 <= len(s) <= 300 and _CUE.search(s) and len(tokens(s)) >= 3 and s not in out:
            out.append(s)
    return out[:limit]


# ------------------------------------------------------------------ retrieval
class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.docs = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for d, tf in zip(self.docs, self.tf):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out


class Dense:
    """Sentence embeddings with fastembed (ONNX, CPU, no PyTorch). Optional."""

    MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, docs: list[str]):
        from fastembed import TextEmbedding   # ImportError when not installed: caller falls back to BM25 only
        self.model = TextEmbedding(self.MODEL)
        self.vecs = [list(v) for v in self.model.embed(docs)]

    def scores(self, query: str) -> list[float]:
        q = list(next(iter(self.model.query_embed(query))))
        return [sum(a * b for a, b in zip(q, v)) for v in self.vecs]


def _ranks(scores: list[float]) -> list[int]:
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    r = [0] * len(scores)
    for pos, i in enumerate(order):
        r[i] = pos
    return r


@dataclass
class Retriever:
    chunks: list[Chunk]
    dense: bool = True
    methods: list[str] = field(default_factory=list)

    def __post_init__(self):
        texts = [c.full for c in self.chunks]
        self.engines = [BM25(texts)]
        self.methods = ["bm25"]
        if self.dense:
            try:
                self.engines.append(Dense(texts))
                self.methods.append("dense:" + Dense.MODEL)
            except Exception:       # not installed, or the model could not be downloaded
                pass

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        """Reciprocal rank fusion of the engines. Chunks with no keyword overlap and no dense signal are dropped."""
        per = [e.scores(query) for e in self.engines]
        fused = [0.0] * len(self.chunks)
        for s in per:
            for i, r in enumerate(_ranks(s)):
                fused[i] += 1 / (60 + r)
        has_signal = [per[0][i] > 0 or len(per) > 1 for i in range(len(self.chunks))]
        order = sorted((i for i in range(len(self.chunks)) if has_signal[i]), key=lambda i: -fused[i])
        return [(self.chunks[i], fused[i]) for i in order[:k]]


def retrieve(chunks: list[Chunk], reqs: list[str], retriever: Retriever, per_req: int = 3, cap: int = 14):
    """Top chunks per requirement. Returns (chunks used, {requirement: [chunk ids]})."""
    support: dict[str, list[str]] = {}
    score: Counter = Counter()
    for r in reqs:
        hits = retriever.search(r, per_req)
        support[r] = [c.id for c, _ in hits]
        for c, s in hits:
            score[c.id] += s
    by_id = {c.id: c for c in chunks}
    used = [by_id[i] for i, _ in score.most_common(cap)]
    used.sort(key=lambda c: int(c.id[1:]))
    return used, support


# ------------------------------------------------------------------ generation
SYSTEM = """You help a job applicant tailor their CV to one job posting.
You get numbered excerpts from the applicant's CV (C1, C2, ...) and the posting.
Use ONLY facts stated in the excerpts. Never add skills, employers, numbers or results that are not in them.
Every item must cite the excerpt ids it relies on in "sources". If a requirement is not supported by any
excerpt, do not write around it: list it in "gaps" instead. Keep the applicant's own wording where possible.
Write in clear, plain English, first person for the cover note, no clichés."""

CLAIM = {"type": "object", "properties": {"text": {"type": "string"},
                                          "sources": {"type": "array", "items": {"type": "string"}}},
         "required": ["text", "sources"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": CLAIM},
        "bullets": {"type": "array", "items": {
            "type": "object",
            "properties": {"requirement": {"type": "string"}, "text": {"type": "string"},
                           "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["requirement", "text", "sources"], "additionalProperties": False}},
        "cover_note": {"type": "array", "items": CLAIM},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "bullets", "cover_note", "gaps"],
    "additionalProperties": False,
}


def prompt(job_title: str, company: str, job_text: str, used: list[Chunk], reqs: list[str]) -> str:
    excerpts = "\n".join(f"[{c.id}] ({c.section or 'CV'}) {c.text}" for c in used)
    req = "\n".join(f"- {r}" for r in reqs) or "(none extracted; use the posting)"
    return (f"JOB: {job_title} at {company}\n\nKEY REQUIREMENTS:\n{req}\n\nPOSTING:\n{job_text[:6000]}\n\n"
            f"CV EXCERPTS:\n{excerpts}\n\n"
            "Write: a 3-sentence CV summary for this job (one claim per sentence); 4-6 CV bullets, each tied to "
            "one requirement; a cover note of 4-6 sentences (about 120 words) that mentions the applicant is "
            "based in Finland (EU) only if an excerpt says so; and the requirements the CV does not support.")


# ------------------------------------------------------------------ grounding check
def _numbers(text: str) -> set[str]:
    return {m.group(0).lower().replace(" ", "").replace(",", ".") for m in _NUM.finditer(text or "")}


def check_claim(text: str, sources: list[str], allowed: dict[str, Chunk]) -> list[str]:
    """Problems with one claim; empty when it is grounded."""
    problems = []
    if not sources:
        return ["cites no CV excerpt"]
    unknown = [s for s in sources if s not in allowed]
    if unknown:
        problems.append("cites excerpts that were not retrieved: " + ", ".join(unknown))
    cited = " ".join(allowed[s].full for s in sources if s in allowed)
    missing = _numbers(text) - _numbers(cited)
    if missing:
        problems.append("numbers not found in the cited excerpts: " + ", ".join(sorted(missing)))
    return problems


def grounding(result: dict, used: list[Chunk]) -> dict:
    allowed = {c.id: c for c in used}
    report = {"claims": 0, "grounded": 0, "issues": []}
    for part in ("summary", "bullets", "cover_note"):
        for item in result.get(part) or []:
            report["claims"] += 1
            probs = check_claim(item.get("text", ""), item.get("sources") or [], allowed)
            item["problems"] = probs
            if probs:
                report["issues"].append({"part": part, "text": item.get("text", ""), "problems": probs})
            else:
                report["grounded"] += 1
    return report


# ------------------------------------------------------------------ orchestration
def tailor(cv_text: str, job_text: str, *, job_title: str = "", company: str = "", api_key: str = "",
           model: str = DEFAULT_MODEL, dense: bool = True, call=None) -> dict:
    chunks = chunk_cv(cv_text)
    if len(chunks) < 3:
        raise ValueError("could not find enough text in the CV")
    reqs = requirements(job_text) or [job_text[:400]]
    retriever = Retriever(chunks, dense=dense)
    used, support = retrieve(chunks, reqs, retriever)
    user = prompt(job_title, company, job_text, used, reqs)
    if call is not None:
        result, usage = call(SYSTEM, user, SCHEMA)
    else:
        result, usage = llm.call_claude(SYSTEM, user, SCHEMA, model=model, api_key=api_key, max_tokens=2000,
                                        timeout=120)
    check = grounding(result, used)
    return {"result": result, "grounding": check, "used": used, "support": support, "requirements": reqs,
            "retrieval": retriever.methods, "usage": usage, "model": model, "chunks": len(chunks)}


def render(out: dict, job_title: str, company: str, url: str = "") -> str:
    r, g = out["result"], out["grounding"]
    mark = lambda item: "" if not item.get("problems") else "  ⚠ " + "; ".join(item["problems"])
    cite = lambda item: " [" + ", ".join(item.get("sources") or []) + "]"
    lines = [f"# {job_title} at {company}", ""]
    if url:
        lines += [url, ""]
    lines += [f"Grounding: **{g['grounded']} of {g['claims']}** claims cite retrieved CV excerpts and use only "
              f"numbers found in them. Retrieval: {', '.join(out['retrieval'])}. "
              f"{len(out['used'])} of {out['chunks']} CV excerpts used.", ""]
    if g["issues"]:
        lines += ["Check these before using them:", ""] + [f"- {i['text']} ({'; '.join(i['problems'])})" for i in g["issues"]] + [""]
    lines += ["## CV summary", ""] + [f"{i['text']}{cite(i)}{mark(i)}" for i in r.get("summary", [])]
    lines += ["", "## CV bullets", ""]
    for b in r.get("bullets", []):
        lines.append(f"- {b['text']}{cite(b)}{mark(b)}\n  - for: *{b['requirement']}*")
    lines += ["", "## Cover note", "", " ".join(i["text"] for i in r.get("cover_note", [])), ""]
    lines += [f"{i['text']}{cite(i)}{mark(i)}" for i in r.get("cover_note", []) if i.get("problems")]
    lines += ["", "## Gaps: requirements your CV does not show", ""] + ([f"- {x}" for x in r.get("gaps", [])] or ["- none"])
    lines += ["", "## CV excerpts the answer was built from", ""] + [f"- **{c.id}** ({c.section or 'CV'}) {c.text}" for c in out["used"]]
    return "\n".join(lines) + "\n"
