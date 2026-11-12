import datetime as dt
import json
from pathlib import Path

import yaml

from radar import pipeline, report
from radar.models import Job
from radar.sources import SourceResult, ats, boards, hackernews
from tests import test_sources as fx

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 11, 12)


def all_jobs():
    return (ats.parse_greenhouse("acme", fx.GREENHOUSE) + ats.parse_lever("acme", fx.LEVER)
            + ats.parse_ashby("acme", fx.ASHBY) + boards.parse_himalayas(fx.HIMALAYAS)
            + boards.parse_remoteok(fx.REMOTEOK) + boards.parse_remotive(fx.REMOTIVE)
            + boards.parse_wwr(fx.WWR) + hackernews.parse_thread(fx.HN))


def test_process_end_to_end():
    kept, funnel = pipeline.process(all_jobs(), CFG, TODAY)
    by = {(j.source, j.company): j for j in kept}
    assert by[("greenhouse", "Acme")].eligibility == "europe"
    assert by[("lever", "acme")].eligibility == "europe"
    assert by[("ashby", "acme")].eligibility == "finland"
    assert by[("himalayas", "Globex")].eligibility == "finland"
    assert by[("himalayas", "Initech")].eligibility == "worldwide"
    assert by[("hackernews", "Acme Robotics")].eligibility == "europe"
    assert ("hackernews", "Globex") not in by            # US only
    assert funnel["us_only"] == 2 and funnel["collected"] == 12 and funnel["matched"] == len(kept)
    assert kept == sorted(kept, key=lambda j: -j.score)


def test_duplicates_keep_the_direct_posting():
    direct = Job("greenhouse", "acme:1", "Acme", "AI Engineer", "https://a", location="Remote - Europe")
    board = Job("remoteok", "9", "ACME", "AI  Engineer", "https://b", location="Worldwide", remote=True)
    kept, funnel = pipeline.process([board, direct], CFG, TODAY)
    assert len(kept) == 1 and kept[0].source == "greenhouse" and funnel["duplicate"] == 1


def test_agencies_are_dropped():
    j = Job("ashby", "x:1", "Jobgether", "AI Engineer", "https://a", location="Remote - Europe")
    kept, funnel = pipeline.process([j], CFG, TODAY)
    assert kept == [] and funnel["agency"] == 1


def test_first_seen_survives_a_source_outage():
    ledger = {}
    a = Job("ashby", "x:1", "Acme", "AI Engineer", "u")
    b = Job("ashby", "x:2", "Acme", "ML Engineer", "u")
    assert pipeline.merge_first_seen([a, b], ledger, dt.date(2026, 11, 10)) == 0      # first run: nothing is "new"
    assert pipeline.merge_first_seen([a], ledger, dt.date(2026, 11, 11)) == 0         # b's source is down
    c = Job("ashby", "x:3", "Acme", "Data Engineer", "u")
    assert pipeline.merge_first_seen([a, b, c], ledger, dt.date(2026, 11, 12)) == 1   # only c is new
    assert b.first_seen == "2026-11-10" and c.first_seen == "2026-11-12"


def test_run_writes_output_and_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [
        SourceResult("greenhouse", ats.parse_greenhouse("acme", fx.GREENHOUSE), detail="1 of 1 boards answered"),
        SourceResult("remoteok", [], ok=False, error="HTTP 503"),
    ])
    out = tmp_path / "docs" / "jobs.json"
    payload = pipeline.run(CFG, {}, out, TODAY)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["id"] == "greenhouse:acme:4012345" and "description" not in saved["jobs"][0]
    assert saved["first_run"] is True and (tmp_path / "docs" / "seen.json").exists()
    text = report.digest(payload, "https://example.com/dash")
    assert "First run" in text and "remoteok: FAILED (HTTP 503)" in text and "https://example.com/dash" in text


def test_run_refuses_to_overwrite_when_everything_is_down(tmp_path, monkeypatch):
    out = tmp_path / "jobs.json"
    out.write_text('{"jobs": [{"id": "keep-me"}]}', encoding="utf-8")
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [SourceResult("ashby", [], ok=False, error="down")])
    try:
        pipeline.run(CFG, {}, out, TODAY)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert "keep-me" in out.read_text(encoding="utf-8")
