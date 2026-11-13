"""LLM reading of job postings, with a check that every answer is backed by the posting's own words.

The rules in eligibility.py look at the location field and a few phrases. Many postings say where you can
work from somewhere in the middle of the description ("you must hold a valid US work authorisation",
"we hire across EU time zones"), or say it in a way no regex anticipates. Claude reads the posting and returns
structured JSON. Two guards keep that honest:

* Evidence must be quoted verbatim. If the quote is not actually in the posting, the answer is discarded
  and the rule-based label stands. This catches the model inventing a justification.
* Answers are cached by a hash of the posting text, so each posting is read once and reruns are free.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .models import Job

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
PROMPT_VERSION = "elig-v1"          # bump when the prompt or schema changes, so the cache is rebuilt
MAX_POSTING_CHARS = 7000

LABELS = ["finland", "europe", "worldwide", "eu_country", "us_only", "other_region", "unclear"]

SYSTEM = """You read job postings for a software engineer who lives in Finland (an EU country) and wants remote work.
Decide where the job can be done from, using only what the posting says. Labels:

- finland: the job is based in Finland or explicitly open to people in Finland or the Nordics.
- europe: remote and open to people across Europe, the EU/EEA, EMEA, or European time zones (CET/EET, UTC+0 to UTC+3).
- worldwide: remote and explicitly open anywhere, with no country restriction.
- eu_country: remote but limited to one or a few named European countries that do not include Finland.
- us_only: limited to the United States or North America (including US work authorisation, US residency or security clearance requirements).
- other_region: limited to another region or country (Canada alone, LATAM, India, APAC, ...).
- unclear: the posting does not say where you can work from.

Rules:
- A restriction anywhere in the text overrides a looser location field (e.g. location "Remote" but "must reside in the US" means us_only).
- Mentions of customers, offices or markets are not restrictions.
- "evidence" must be copied exactly, character for character, from the posting: the shortest sentence or fragment that decides the label. Use "" only when the label is unclear.
- employment: "contractor" if it is a contract/freelance role, "employee" if it is a permanent job, "either" if both are offered, "unknown" otherwise.
- languages_required: human languages the posting requires besides English (e.g. ["Finnish"]). Empty if none.
- min_years: minimum years of professional experience asked for, or -1 if not stated.
- summary: one plain sentence (max 25 words) saying what the job actually is."""

SCHEMA = {
    "type": "object",
    "properties": {
        "eligibility": {"type": "string", "enum": LABELS},
        "evidence": {"type": "string"},
        "employment": {"type": "string", "enum": ["employee", "contractor", "either", "unknown"]},
        "languages_required": {"type": "array", "items": {"type": "string"}},
        "min_years": {"type": "integer"},
        "summary": {"type": "string"},
    },
    "required": ["eligibility", "evidence", "employment", "languages_required", "min_years", "summary"],
    "additionalProperties": False,
}


class LLMError(Exception):
    pass


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    failed: int = 0
    unverified: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def cost_usd(self, in_price: float = 0.10, out_price: float = 0.50) -> float:
        """Haiku 5.5 list prices per million tokens (prompts under 100k tokens), November 2026."""
        return self.input_tokens / 1e6 * in_price + self.output_tokens / 1e6 * out_price

    def as_dict(self) -> dict:
        return {"calls": self.calls, "cached": self.cached, "failed": self.failed, "unverified": self.unverified,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd(), 6)}


# ------------------------------------------------------------------ client
def call_claude(system: str, user: str, schema: dict, *, model: str, api_key: str, max_tokens: int = 600,
                tries: int = 4, timeout: float = 60) -> tuple[dict, dict]:
    """One Messages API call with structured JSON output. Returns (parsed JSON, usage)."""
    body = {
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    last = ""
    for attempt in range(tries):
        try:
            r = requests.post(API_URL, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 529):
            last = f"HTTP {r.status_code}"
            time.sleep(float(r.headers.get("retry-after") or 5 * (attempt + 1)))
            continue
        if not r.ok:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if data.get("stop_reason") in ("refusal", "max_tokens"):
            raise LLMError(f"stop_reason {data['stop_reason']}")
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        try:
            return json.loads(text), data.get("usage", {})
        except ValueError as e:
            raise LLMError("response was not JSON") from e
    raise LLMError(last or "no response")


# ------------------------------------------------------------------ evidence check
_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _norm(s: str) -> str:
    return _SPACE.sub(" ", (s or "").translate(_QUOTES)).strip().lower()


def evidence_found(evidence: str, *texts: str) -> bool:
    """True when the quote appears in the posting (ignoring case, whitespace and curly quotes).

    Long quotes may be trimmed by the model at the ends, so a quote of 12+ words also passes when its
    middle 80% is present.
    """
    ev = _norm(evidence).strip(" .\"'")
    if not ev:
        return False
    hay = _norm(" ".join(texts))
    if ev in hay:
        return True
    words = ev.split()
    if len(words) >= 12:
        cut = max(1, len(words) // 10)
        return " ".join(words[cut:-cut]) in hay
    return False


# ------------------------------------------------------------------ one posting
def posting_text(job: Job) -> str:
    return (f"Title: {job.title}\nCompany: {job.company}\nLocation field: {job.location or '(none)'}\n"
            f"Employment type field: {job.employment_type or '(none)'}\n\n"
            f"Description:\n{(job.description or '(none)')[:MAX_POSTING_CHARS]}")


def cache_key(job: Job, model: str) -> str:
    raw = f"{PROMPT_VERSION}|{model}|{job.title}|{job.location}|{(job.description or '')[:MAX_POSTING_CHARS]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def validate(result: dict, job: Job) -> dict:
    """Normalise the model's answer and attach `verified`."""
    out = {
        "eligibility": str(result.get("eligibility", "")).lower().strip(),
        "evidence": str(result.get("evidence", "")).strip(),
        "employment": str(result.get("employment", "unknown")).lower().strip(),
        "languages_required": [str(x).strip() for x in result.get("languages_required") or [] if str(x).strip()],
        "min_years": result.get("min_years", -1) if isinstance(result.get("min_years"), int) else -1,
        "summary": str(result.get("summary", "")).strip()[:300],
    }
    if out["eligibility"] not in LABELS:
        out["verified"] = False
    elif out["eligibility"] == "unclear":
        out["verified"] = True       # nothing to quote
    else:
        out["verified"] = evidence_found(out["evidence"], job.title, job.location, job.employment_type, job.description)
    if not (0 <= out["min_years"] <= 20):
        out["min_years"] = -1
    out["languages_required"] = [l for l in out["languages_required"] if l.lower() not in ("english", "en")]
    return out


@dataclass
class Reader:
    """Reads postings with Claude, using and updating a JSON cache file."""

    model: str = DEFAULT_MODEL
    api_key: str = ""
    cache_path: Path | None = None
    max_calls: int = 600
    workers: int = 4
    call: object = None   # injectable for tests: (system, user, schema) -> (dict, usage)
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self):
        self.api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.cache: dict = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}
        self._budget = self.max_calls
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or self.call is not None

    def _call(self, job: Job) -> tuple[dict, dict]:
        if self.call is not None:
            return self.call(SYSTEM, posting_text(job), SCHEMA)
        return call_claude(SYSTEM, posting_text(job), SCHEMA, model=self.model, api_key=self.api_key)

    def read(self, job: Job, today: str = "") -> dict | None:
        """Cached answer, or a fresh one while the call budget lasts. None when neither is available."""
        key = cache_key(job, self.model)
        hit = self.cache.get(key)
        if hit:
            with self.usage.lock:
                self.usage.cached += 1
            hit["seen"] = today or hit.get("seen", "")
            return hit
        with self._lock:
            if self._budget <= 0:
                return None
            self._budget -= 1
        try:
            raw, usage = self._call(job)
        except LLMError:
            with self.usage.lock:
                self.usage.failed += 1
            return None
        res = validate(raw, job)
        res["seen"] = today
        with self.usage.lock:
            self.usage.calls += 1
            self.usage.unverified += not res["verified"]
            self.usage.input_tokens += int(usage.get("input_tokens", 0))
            self.usage.output_tokens += int(usage.get("output_tokens", 0))
        with self._lock:
            self.cache[key] = res
        return res

    def read_all(self, jobs: list[Job], today: str = "") -> list[dict | None]:
        with cf.ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda j: self.read(j, today), jobs))

    def save(self, today: dt.date | None = None, keep_days: int = 120) -> None:
        if not self.cache_path:
            return
        if today:
            cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
            self.cache = {k: v for k, v in self.cache.items() if (v.get("seen") or "9999") >= cutoff}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def apply(job: Job, res: dict) -> None:
    """Fold a verified LLM answer into the job. Unverified answers only contribute the summary."""
    job.llm_summary = res.get("summary", "")
    if not res.get("verified"):
        job.llm_status = "unverified"
        return
    job.llm_status = "checked"
    job.rule_eligibility = job.eligibility
    if res["eligibility"] != job.eligibility:
        job.eligibility = res["eligibility"]
        job.eligibility_reason = (f'AI read the posting: "{res["evidence"][:160]}"' if res["evidence"]
                                  else "AI read the posting: it does not say where you can work from")
    elif res["evidence"]:
        job.eligibility_reason += f'; confirmed by AI: "{res["evidence"][:120]}"'
    job.llm_employment = res.get("employment", "unknown")
    job.llm_languages = res.get("languages_required", [])
    job.llm_min_years = res.get("min_years", -1)
