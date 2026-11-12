import datetime as dt
from pathlib import Path

import pytest
import yaml

from radar import scoring
from radar.models import Job

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))


def job(title, description="", **kw):
    return Job("test", "1", "Acme", title, "https://example.com", description=description, **kw)


@pytest.mark.parametrize("title,role", [
    ("AI Engineer", "AI / LLM Engineer"),
    ("Senior LLM Engineer, Agents", "AI / LLM Engineer"),
    ("Machine Learning Engineer", "ML Engineer / MLOps"),
    ("MLOps Engineer", "ML Engineer / MLOps"),
    ("Backend Engineer (Python)", "Backend Engineer"),
    ("Software Engineer, Backend", "Backend Engineer"),
    ("Full-Stack Engineer", "Full-Stack Engineer"),
    ("Forward Deployed Engineer", "Forward Deployed / Solutions Engineer"),
    ("Freelance AI Trainer - LLM Evaluation", "AI Trainer / Evaluator"),
    ("Data Engineer", "Data Engineer"),
])
def test_role_match(title, role):
    assert scoring.match_role(job(title), CFG) == role


@pytest.mark.parametrize("title", [
    "Marketing Intern",                 # "RN "-style substring bugs: intern is not a nurse, and not ours either
    "Director, Biostatistics",          # must not match "ios" or anything else
    "Subscriber Growth Lead",
    "Engineering Manager, AI",          # excluded: manager
    "Senior Java Developer",            # excluded: java
    "Sales Engineer",                   # excluded: sales
    "Trailer Mechanic",                 # "ai" inside a word
    "Storage Engineer",                 # "rag" inside a word
    "Account Executive",
])
def test_role_rejects(title):
    assert scoring.match_role(job(title), CFG) is None


def test_javascript_is_not_java():
    assert scoring.match_role(job("Full Stack Engineer (JavaScript)"), CFG) == "Full-Stack Engineer"


def test_free_text_posts_match_on_body():
    j = job("Acme | Remote (EU) | Full-time", "We are hiring a backend engineer and a sales lead.", match_full_text=True)
    assert scoring.match_role(j, CFG) == "Backend Engineer"


def test_levels():
    assert scoring.level_of("Junior Backend Engineer", CFG) == "junior"
    assert scoring.level_of("Senior Backend Engineer", CFG) == "senior"
    assert scoring.level_of("Staff ML Engineer", CFG) == "staff"
    assert scoring.level_of("Backend Engineer", CFG) == "mid"


def test_years_required():
    assert scoring.years_required("You have 5+ years of Python and 2 years of ML.") == 5
    assert scoring.years_required("3-5 years experience") == 3
    assert scoring.years_required("Founded 20 years ago") is None  # capped at 15 to skip company-age noise
    assert scoring.years_required("no numbers here") is None


def test_junior_europe_python_job_beats_staff_unclear_job():
    today = dt.date(2026, 11, 12)
    good = job("Junior AI Engineer", "Python, FastAPI, RAG, LLM, LangChain. Contract possible.", posted="2026-11-10")
    good.role, good.eligibility = "AI / LLM Engineer", "europe"
    bad = job("Staff AI Engineer", "10+ years building distributed systems.", posted="2026-08-01")
    bad.role, bad.eligibility = "AI / LLM Engineer", "unclear"
    scoring.score(good, CFG, today)
    scoring.score(bad, CFG, today)
    assert good.score > bad.score + 30
    assert 0 <= bad.score <= 100 and 0 <= good.score <= 100
    assert any("skills" in r for r in good.reasons)
    assert any("10+ years" in r for r in bad.reasons)
