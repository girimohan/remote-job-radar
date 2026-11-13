import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from radar import llm, pipeline
from radar.models import Job

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 11, 12)

US_DESC = ("We are a remote-first company building AI agents with Python and FastAPI. "
           "Candidates must be authorized to work in the United States. We offer equity and a laptop.")
EU_DESC = "Fully remote. We hire engineers anywhere in Europe within CET +/- 2 hours. Freelance contracts welcome."


def job(title="AI Engineer", location="Remote", description=US_DESC, **kw):
    return Job("ashby", "acme:1", "Acme", title, "https://example.com", location=location, description=description, **kw)


def fake(answers):
    """A stand-in for the API: returns answers in order and records what it was asked."""
    calls = []

    def call(system, user, schema):
        calls.append(user)
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(a, Exception):
            raise a
        return a, {"input_tokens": 1000, "output_tokens": 100}

    call.calls = calls
    return call


def answer(label, evidence, **kw):
    return {"eligibility": label, "evidence": evidence, "employment": kw.get("employment", "employee"),
            "languages_required": kw.get("languages", []), "min_years": kw.get("min_years", -1),
            "summary": kw.get("summary", "Builds AI agents.")}


# ---------------------------------------------------------------- evidence check
def test_evidence_must_be_in_the_posting():
    assert llm.evidence_found("must be authorized to work in the United States", US_DESC)
    assert llm.evidence_found("MUST BE   authorized to work\nin the United States.", US_DESC)   # case, spacing
    assert llm.evidence_found("we’re hiring “anywhere”", 'We\'re hiring "anywhere" today')    # curly quotes
    assert not llm.evidence_found("must be a US citizen", US_DESC)                          # invented
    assert not llm.evidence_found("", US_DESC)


def test_long_quote_trimmed_at_the_ends_still_counts():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    quoted = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    assert llm.evidence_found(quoted, text)


def test_validate_normalises_and_verifies():
    j = job()
    ok = llm.validate(answer("US_ONLY", "authorized to work in the United States", languages=["English", "Finnish"],
                             min_years=40), j)
    assert ok["eligibility"] == "us_only" and ok["verified"] and ok["languages_required"] == ["Finnish"]
    assert ok["min_years"] == -1                                   # out of range
    assert not llm.validate(answer("us_only", "requires US citizenship"), j)["verified"]
    assert not llm.validate(answer("mars", ""), j)["verified"]
    assert llm.validate(answer("unclear", ""), j)["verified"]      # nothing to quote


# ---------------------------------------------------------------- reader: cache and budget
def test_reader_caches_and_respects_budget(tmp_path):
    call = fake([answer("us_only", "authorized to work in the United States")])
    r = llm.Reader(call=call, cache_path=tmp_path / "c.json", max_calls=2)
    a, b, c = job(), job(title="ML Engineer"), job(title="Data Engineer")
    assert r.read(a)["eligibility"] == "us_only"
    assert r.read(a)["eligibility"] == "us_only"        # cached: no second call
    assert len(call.calls) == 1 and r.usage.cached == 1
    assert r.read(b) is not None and r.read(c) is None   # budget of 2 used up
    r.save(TODAY)
    again = llm.Reader(call=fake([]), cache_path=tmp_path / "c.json", max_calls=0)
    assert again.read(a)["eligibility"] == "us_only"     # survives a restart


def test_reader_counts_failures_and_unverified():
    r = llm.Reader(call=fake([llm.LLMError("HTTP 400"), answer("worldwide", "hire from anywhere")]))
    assert r.read(job()) is None and r.usage.failed == 1
    res = r.read(job(title="Other"))
    assert res["verified"] is False and r.usage.unverified == 1
    assert r.usage.as_dict()["cost_usd"] == pytest.approx(0.00015)


def test_cache_key_changes_with_posting_and_model():
    assert llm.cache_key(job(), "m1") != llm.cache_key(job(description="x"), "m1")
    assert llm.cache_key(job(), "m1") != llm.cache_key(job(), "m2")


# ---------------------------------------------------------------- apply + pipeline
def test_apply_overrides_rules_only_with_verified_evidence():
    j = job()
    j.eligibility, j.eligibility_reason = "unclear", "posting does not say"
    llm.apply(j, llm.validate(answer("us_only", "authorized to work in the United States"), j))
    assert j.eligibility == "us_only" and j.rule_eligibility == "unclear" and j.llm_status == "checked"
    assert "authorized to work" in j.eligibility_reason

    k = job()
    k.eligibility = "unclear"
    llm.apply(k, llm.validate(answer("us_only", "US citizens only"), k))
    assert k.eligibility == "unclear" and k.llm_status == "unverified" and k.llm_summary


HIDDEN_US = ("Remote-first team building AI agents in Python. This role requires residence in one of our 38 "
             "approved states and a valid SSN for payroll.")


def test_llm_stage_drops_hidden_us_only_and_rescores():
    us = job(title="AI Engineer", description=HIDDEN_US)                   # rules cannot see this restriction
    eu = Job("ashby", "acme:2", "Beta", "Backend Engineer", "https://e", location="Remote", description=EU_DESC)
    kept, funnel = pipeline.process([us, eu], CFG, TODAY)
    assert {j.eligibility for j in kept} == {"unclear", "europe"}
    before = {j.company: j.score for j in kept}
    def by_posting(system, user, schema):            # answer depends on which posting is asked about
        if "Company: Acme" in user:
            return answer("us_only", "requires residence in one of our 38 approved states and a valid SSN"), {}
        return answer("europe", "anywhere in Europe within CET +/- 2 hours", employment="contractor", min_years=2), {}

    reader = llm.Reader(call=by_posting)
    out = pipeline.llm_stage(kept, CFG, reader, TODAY, funnel)
    assert [j.company for j in out] == ["Beta"] and funnel["llm_dropped"] == 1 and funnel["matched"] == 1
    beta = out[0]
    assert beta.llm_employment == "contractor" and "contract or part-time possible" in beta.reasons
    assert beta.score >= before["Beta"]


def test_required_language_costs_points():
    j = job(description="Remote in Finland. Sujuva suomen kielen taito vaaditaan. Python.", location="Remote, Finland")
    kept, funnel = pipeline.process([j], CFG, TODAY)
    before = kept[0].score
    reader = llm.Reader(call=fake([answer("finland", "Remote, Finland", languages=["Finnish"])]))
    out = pipeline.llm_stage(kept, CFG, reader, TODAY, funnel)
    assert out[0].score == max(0, before - CFG["language_penalty"]) and "requires Finnish" in out[0].reasons


def test_run_without_key_uses_rules_only(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from radar.sources import SourceResult
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [SourceResult("ashby", [job(location="Remote - Europe")])])
    payload = pipeline.run(CFG, {}, tmp_path / "jobs.json", TODAY)
    assert payload["llm"] == {"enabled": False} and payload["jobs"][0]["llm_status"] == ""
    assert not (tmp_path / "llm_cache.json").exists()


def test_run_with_reader_reports_usage(tmp_path, monkeypatch):
    from radar.sources import SourceResult
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [SourceResult("ashby", [job(location="Remote - Europe")])])
    reader = llm.Reader(call=fake([answer("us_only", "authorized to work in the United States")]),
                        cache_path=tmp_path / "llm_cache.json")
    payload = pipeline.run(CFG, {}, tmp_path / "jobs.json", TODAY, reader=reader)
    assert payload["jobs"] == [] and payload["funnel"]["llm_dropped"] == 1
    assert payload["llm"]["calls"] == 1 and payload["llm"]["enabled"]
    assert json.loads((tmp_path / "llm_cache.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- HTTP client
class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.ok = 200 <= status < 300
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_call_claude_sends_structured_output_request_and_retries(monkeypatch):
    sent = []
    responses = [
        FakeResponse(529, {"type": "error"}, {"retry-after": "0"}),
        FakeResponse(200, {"content": [{"type": "text", "text": json.dumps(answer("europe", "EMEA"))}],
                           "stop_reason": "end_turn", "usage": {"input_tokens": 900, "output_tokens": 80}}),
    ]

    def post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return responses.pop(0)

    monkeypatch.setattr(llm.requests, "post", post)
    out, usage = llm.call_claude("sys", "posting", llm.SCHEMA, model="claude-haiku-5-5", api_key="k")
    assert out["eligibility"] == "europe" and usage["output_tokens"] == 80 and len(sent) == 2
    body, headers = sent[1][1], sent[1][2]
    assert body["output_config"]["format"]["type"] == "json_schema" and body["model"] == "claude-haiku-5-5"
    assert headers["x-api-key"] == "k" and headers["anthropic-version"] == "2023-06-01"


def test_call_claude_rejects_refusals_and_bad_requests(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse(200, {"content": [], "stop_reason": "refusal"}))
    with pytest.raises(llm.LLMError):
        llm.call_claude("s", "u", llm.SCHEMA, model="m", api_key="k")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: FakeResponse(401, {"error": "bad key"}))
    with pytest.raises(llm.LLMError, match="401"):
        llm.call_claude("s", "u", llm.SCHEMA, model="m", api_key="k")
