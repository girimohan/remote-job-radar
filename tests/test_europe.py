"""Sources and rules added for Finnish and European coverage. Payload shapes copied from live responses, 2026-11-12."""
import datetime as dt
from pathlib import Path

import yaml

from radar import language, pipeline, scoring
from radar.eligibility import classify
from radar.models import Job
from radar.sources import ats, boards

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 11, 12)

SMARTRECRUITERS = {"offset": 0, "limit": 100, "totalFound": 2, "content": [
    {"id": "744000161347605", "name": "Backend Engineer", "company": {"identifier": "Wise", "name": "Wise"},
     "releasedDate": "2026-11-12T16:53:33.993Z",
     "location": {"city": "Helsinki", "country": "fi", "remote": False, "hybrid": True, "fullLocation": "Helsinki, , Finland"},
     "typeOfEmployment": {"id": "permanent", "label": "Full-time"}, "language": {"code": "en", "label": "English"}},
    {"id": "744000161347606", "name": "Ohjelmistokehittäjä", "company": {"identifier": "Tieto2", "name": "Tieto"},
     "releasedDate": "2026-11-10T10:00:00.000Z",
     "location": {"city": "Espoo", "country": "fi", "remote": False, "hybrid": False, "fullLocation": "Espoo, , Finland"},
     "typeOfEmployment": {"label": "Full-time"}, "language": {"code": "fi", "label": "Finnish"}},
]}
JOBICY = {"apiVersion": "2", "jobCount": 2, "jobs": [
    {"id": 161644, "url": "https://jobicy.com/jobs/161644-ai-engineer", "jobTitle": "AI Engineer &amp; Researcher",
     "companyName": "Energy Exemplar", "jobIndustry": ["Engineering"], "jobType": ["Full-Time"], "jobGeo": "Sweden,  UK",
     "jobLevel": "Midweight", "jobExcerpt": "x", "jobDescription": "<p>We build <strong>LLM</strong> tools.</p>",
     "pubDate": "2026-11-12T13:29:26+00:00", "salaryMin": 60000, "salaryMax": 80000, "salaryCurrency": "EUR"},
    {"id": 161645, "url": "https://jobicy.com/jobs/161645-backend", "jobTitle": "Backend Developer",
     "companyName": "Initrode", "jobType": ["Contract"], "jobGeo": "Europe", "jobDescription": "<p>Python</p>",
     "pubDate": "2026-11-11T09:00:00+00:00"},
]}
ARBEITNOW = {"data": [
    {"slug": "software-developer-berlin-1", "company_name": "Beispiel GmbH", "title": "Softwareentwickler Backend (m/w/d)",
     "description": "<p>Wir suchen dich für unser Team und bieten dir eine spannende Aufgabe mit Python. "
                    "Du arbeitest mit uns an der Plattform und bist für die Entwicklung der Services verantwortlich. "
                    "Wir sind ein Unternehmen, das die Zukunft gestaltet und eine offene Kultur lebt.</p>",
     "remote": True, "url": "https://www.arbeitnow.com/jobs/companies/beispiel/software-developer-berlin-1",
     "tags": ["IT"], "job_types": ["Permanent"], "location": "Bochum", "created_at": 1794490277},
    {"slug": "office-manager-2", "company_name": "Beispiel GmbH", "title": "Backend Engineer", "description": "<p>x</p>",
     "remote": False, "url": "https://www.arbeitnow.com/jobs/x", "tags": [], "job_types": [], "location": "Berlin",
     "created_at": 1794490277},
], "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"}, "meta": {"per_page": 300}}
WORKINGNOMADS = [
    {"url": "https://www.workingnomads.com/job/go/2016531/", "title": "Senior React Full-stack Developer",
     "description": "<p>Join us</p>", "company_name": "Lemon.io", "category_name": "Development",
     "tags": "react,python,nodejs", "location": "Time zone: CET (+/- 3 hours)", "pub_date": "2026-11-12T06:47:52-05:00"},
]


def test_smartrecruiters():
    a, b = ats.parse_smartrecruiters("wise", SMARTRECRUITERS)
    assert a.location == "Helsinki, Finland" and a.language == "en" and a.posted == "2026-11-12"
    assert a.url == "https://jobs.smartrecruiters.com/Wise/744000161347605" and "hybrid" in a.employment_type
    assert b.language == "fi" and classify(b)[0] == "finland"


def test_jobicy():
    a, b = boards.parse_jobicy(JOBICY)
    assert a.title == "AI Engineer & Researcher" and a.location == "Sweden, UK" and a.salary == "EUR 60,000-80,000"
    assert a.description == "We build LLM tools." and classify(a)[0] == "eu_country"
    assert b.employment_type == "Contract" and classify(b)[0] == "europe"


def test_arbeitnow_unknown_town_counts_as_one_european_country():
    a, b = boards.parse_arbeitnow(ARBEITNOW)
    assert a.remote is True and a.posted == "2026-11-12"
    assert classify(a)[0] == "eu_country"          # "Bochum" is not in our place lists; the board is European
    assert b.remote is None and classify(b)[0] == "not_remote"


def test_workingnomads():
    (j,) = boards.parse_workingnomads(WORKINGNOMADS)
    assert j.company == "Lemon.io" and "python" in j.description and j.posted == "2026-11-12"
    assert classify(j)[0] == "europe"              # CET time zone
    assert boards.parse_workingnomads({"detail": "error"}) == []


def test_real_board_location_strings():
    c = lambda loc: classify(Job("t", "1", "c", "Engineer", "u", location=loc, remote=True))[0]
    assert c("Europe, LATAM, APAC, the U.S., Canada") == "europe"
    assert c("United States only (ShyftOff-approved states)") == "us_only"
    assert c("Anywhere in the world") == "worldwide"
    assert c("Japan - Remote") == "other_region"
    assert c("Canada, Mexico, USA") == "us_only"
    # Finland inside a long country list means remote across Europe, not a Finland-based job.
    assert c("Switzerland, Austria, Belgium, Denmark, Finland, France, Germany, Ireland, Netherlands, United States") == "europe"
    assert c("Helsinki, Finland") == "finland"


def test_place_in_title_when_location_is_bare():
    j = lambda title: classify(Job("t", "1", "c", title, "u", location="Remote"))[0]
    assert j("Software Engineer - Helsinki, Finland") == "finland"
    assert j("Senior Engineer LATAM") == "other_region"
    assert j("AI Engineer (EMEA)") == "europe"
    assert j("Global Payments Engineer") == "unclear"      # "Global" describes the job here, not the place


def test_language_detection():
    fi = ("Etsimme kokenutta ohjelmistokehittäjää tiimiimme. Sinulla on kokemusta Pythonista ja haemme henkilöä, "
          "joka on kiinnostunut tekoälystä sekä pilvipalveluista. Tarjoamme joustavan työn ja hyvät edut. "
          "Meillä on avoin kulttuuri ja työ on monipuolista. Tehtävässä pääset kehittämään osaamistasi.")
    en = ("We are looking for an engineer to join our team. You will work with Python and our platform, and you "
          "are comfortable with the cloud. We offer flexible work for the right person, and the role is remote.")
    assert language.detect("Ohjelmistokehittäjä", fi) == "fi"
    assert language.detect("Software Engineer", en) == "en"
    assert language.detect("Softwareentwickler (m/w/d)", ARBEITNOW["data"][0]["description"]) == "de"
    assert language.detect("Ohjelmistokehittäjä") == "fi"                 # title only
    assert language.detect("Software Engineer (m/f/d)") == "en"           # English ad from a German company
    assert language.detect("Werkstudent Softwareentwicklung") == "de"


def test_finnish_titles_match_roles_and_lose_points():
    j = Job("t", "1", "Tieto", "Ohjelmistokehittäjä", "u", location="Espoo, Finland")
    assert scoring.match_role(j, CFG) == "Backend Engineer"
    kept, _ = pipeline.process([j, Job("t", "2", "Wise", "Software Developer", "u", location="Helsinki, Finland")], CFG, TODAY)
    by = {k.company: k for k in kept}
    assert by["Tieto"].language == "fi" and by["Wise"].language == "en"
    assert by["Wise"].score - by["Tieto"].score == CFG["language_penalty"]
    assert any("Finnish" in r for r in by["Tieto"].reasons)


def test_same_job_in_many_cities_is_collapsed():
    cities = ["Austin", "Berlin", "London", "Helsinki, Finland"]
    jobs = [Job("greenhouse", f"acme:{i}", "Acme", f"Backend Engineer - {c}", "u", location=f"Remote - {c}")
            for i, c in enumerate(cities)]
    jobs.append(Job("greenhouse", "acme:9", "Acme", "Backend Engineer - Payments", "u", location="Remote - Europe"))
    kept, funnel = pipeline.process(jobs, CFG, TODAY)
    titles = sorted(k.title for k in kept)
    assert titles == ["Backend Engineer - Helsinki, Finland", "Backend Engineer - Payments"]   # best location kept
    finland = next(k for k in kept if "Helsinki" in k.title)
    assert finland.variants == 3 and any("3 locations" in r for r in finland.reasons)        # Austin is US-only, dropped earlier


def test_payload_carries_portal_links(tmp_path, monkeypatch):
    from radar.sources import SourceResult
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [SourceResult("jobicy", boards.parse_jobicy(JOBICY))])
    payload = pipeline.run(CFG, {}, tmp_path / "jobs.json", TODAY)
    assert {p["name"] for p in payload["portals"]} >= {"Duunitori", "Työmarkkinatori"}
    assert all("{q}" in p["url"] for p in payload["portals"])
    assert payload["role_searches"]["AI / LLM Engineer"] == "AI engineer"
    assert {"language", "variants"} <= set(payload["jobs"][0])
