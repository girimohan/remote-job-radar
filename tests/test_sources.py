"""Parsers are tested against payloads shaped like each API's documented response."""
from radar.sources import ats, boards, hackernews

GREENHOUSE = {"jobs": [{
    "id": 4012345, "title": "Backend Engineer", "company_name": "Acme",
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/4012345", "location": {"name": "Remote - Europe"},
    "updated_at": "2026-11-10T09:00:00-05:00", "first_published": "2026-11-01T09:00:00-05:00",
    "content": "&lt;p&gt;We use &lt;strong&gt;Python&lt;/strong&gt; &amp;amp; FastAPI.&lt;/p&gt;",
}]}
LEVER = [{
    "id": "abc-123", "text": "ML Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc-123",
    "categories": {"location": "Berlin", "allLocations": ["Berlin", "Remote - EMEA"], "commitment": "Full-time", "team": "ML"},
    "workplaceType": "remote", "createdAt": 1794268800000, "descriptionPlain": "Build models.",
    "lists": [{"text": "Requirements", "content": "<li>PyTorch</li>"}], "additionalPlain": "EU time zones.",
    "salaryRange": {"min": 70000, "max": 90000, "currency": "EUR", "interval": "per-year-salary"},
}]
ASHBY = {"jobs": [
    {"id": "u-1", "title": "AI Engineer", "location": "Remote", "isRemote": True, "isListed": True,
     "secondaryLocations": [{"location": "Helsinki"}], "employmentType": "FullTime",
     "address": {"postalAddress": {"addressCountry": "Finland"}}, "publishedAt": "2026-11-09T12:00:00.000+00:00",
     "jobUrl": "https://jobs.ashbyhq.com/acme/u-1", "descriptionPlain": "RAG and LLM work.",
     "compensation": {"compensationTierSummary": "€60K – €80K"}},
    {"id": "u-3", "title": "Backend Engineer", "location": "Remote, AMER", "isRemote": True, "isListed": True,
     "address": {"postalAddress": {}}, "jobUrl": "https://jobs.ashbyhq.com/acme/u-3",
     "compensation": {"compensationTierSummary": None}},
    {"id": "u-2", "title": "Hidden", "isListed": False},
]}
HIMALAYAS = {"totalCount": 2, "offset": 0, "limit": 20, "jobs": [
    {"title": "Python Developer", "companyName": "Globex", "employmentType": "Full Time", "minSalary": 60000,
     "maxSalary": 80000, "currency": "USD", "locationRestrictions": ["Finland", "Germany"],
     "timezoneRestrictions": [1, 2], "description": "<p>Django</p>", "pubDate": 1794268800,
     "applicationLink": "https://himalayas.app/companies/globex/jobs/python-developer",
     "guid": "https://himalayas.app/companies/globex/jobs/python-developer"},
    {"title": "LLM Engineer", "companyName": "Initech", "locationRestrictions": [], "description": "x",
     "pubDate": 1794268800, "applicationLink": "https://himalayas.app/x", "guid": "https://himalayas.app/x"},
]}
REMOTEOK = [
    {"last_updated": 1794268800, "legal": "API Terms of Service: please link back..."},
    {"id": "1130001", "slug": "ai-engineer-acme", "date": "2026-11-10T08:00:00+00:00", "company": "Acme",
     "position": "AI Engineer", "tags": ["python", "llm"], "description": "<p>Agents</p>", "location": "Worldwide",
     "salary_min": 90000, "salary_max": 120000, "url": "https://remoteOK.com/remote-jobs/1130001",
     "apply_url": "https://remoteOK.com/remote-jobs/1130001"},
]
REMOTIVE = {"job-count": 1, "jobs": [{
    "id": 2099001, "url": "https://remotive.com/remote-jobs/software-dev/backend-2099001", "title": "Backend Developer",
    "company_name": "Hooli", "category": "Software Development", "job_type": "full_time",
    "publication_date": "2026-11-08T10:00:00", "candidate_required_location": "Europe", "salary": "$80k - $100k",
    "description": "<p>FastAPI</p>"}]}
WWR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss"><channel><title>We Work Remotely</title>
<item><title>Pied Piper: Full-Stack Engineer</title><region>Anywhere in the World</region><category>Full-Stack Programming</category>
<type>Full-Time</type><description>&lt;p&gt;React and Node&lt;/p&gt;</description><pubDate>Tue, 10 Nov 2026 14:00:00 +0000</pubDate>
<guid>https://weworkremotely.com/remote-jobs/pied-piper-full-stack-engineer</guid>
<link>https://weworkremotely.com/remote-jobs/pied-piper-full-stack-engineer</link></item>
<item><title>No colon title</title><region>USA Only</region><link>https://weworkremotely.com/remote-jobs/x</link></item>
</channel></rss>"""
HN = {"id": 50338728, "title": "Ask HN: Who is hiring? (November 2026)", "children": [
    {"id": 1, "author": "a", "created_at": "2026-11-02T16:00:00.000Z",
     "text": "Acme Robotics | Backend Engineer | REMOTE (EU) | Full-time<p>We build things with Python &amp; FastAPI.<p>Email jobs@acme.example"},
    {"id": 2, "author": "b", "created_at": "2026-11-02T16:00:00.000Z",
     "text": "Onsite Corp | Engineer | San Francisco | ONSITE<p>No remote, sorry."},
    {"id": 3, "author": None, "text": None},
    {"id": 4, "author": "d", "created_at": "2026-11-02T17:00:00.000Z",
     "text": "Globex - Founding Engineer - Remote (US only)<p>LLM agents."},
]}


def test_greenhouse():
    (j,) = ats.parse_greenhouse("acme", GREENHOUSE)
    assert j.uid == "greenhouse:acme:4012345" and j.company == "Acme" and j.location == "Remote - Europe"
    assert j.posted == "2026-11-01" and j.description == "We use Python & FastAPI."


def test_lever():
    (j,) = ats.parse_lever("acme", LEVER)
    assert j.remote is True and j.location == "Berlin; Remote - EMEA" and j.posted == "2026-11-10"
    assert "PyTorch" in j.description and "EU time zones" in j.description and j.salary == "EUR 70,000-90,000"


def test_lever_unknown_board_is_empty():
    assert ats.parse_lever("nope", {"ok": False, "error": "Document not found"}) == []


def test_real_world_location_strings():
    # Strings seen in live responses on 2026-11-12.
    from radar.eligibility import classify
    from radar.models import Job
    c = lambda loc: classify(Job("t", "1", "c", "t", "u", location=loc, remote=True))[0]
    assert c("Remote, AMER") == "us_only"
    assert c("LATAM, Europe, USA, Canada, APAC") == "europe"
    assert c("Any State") == "other_region"
    assert c("Warsaw") == "eu_country"
    assert c("") == "unclear"


def test_ashby_skips_unlisted_and_joins_locations():
    j, k = ats.parse_ashby("acme", ASHBY)
    assert k.location == "Remote, AMER" and k.salary == ""
    assert j.location == "Remote; Finland; Helsinki" and j.remote is True and j.salary == "€60K – €80K"


def test_himalayas():
    a, b = boards.parse_himalayas(HIMALAYAS)
    assert a.restrictions == ["Finland", "Germany"] and a.salary == "USD 60,000-80,000" and a.posted == "2026-11-10"
    assert b.restrictions == [] and b.location == "Worldwide"


def test_remoteok_skips_legal_notice():
    (j,) = boards.parse_remoteok(REMOTEOK)
    assert j.title == "AI Engineer" and "llm" in j.description and j.salary == "USD 90,000-120,000"


def test_remotive():
    (j,) = boards.parse_remotive(REMOTIVE)
    assert j.location == "Europe" and j.employment_type == "full time" and j.description == "FastAPI"


def test_wwr():
    a, b = boards.parse_wwr(WWR)
    assert a.company == "Pied Piper" and a.title == "Full-Stack Engineer" and a.location == "Anywhere in the World"
    assert a.posted == "2026-11-10" and a.description == "React and Node"
    assert b.company == "" and b.title == "No colon title"


def test_hackernews_keeps_remote_posts_only():
    a, b = hackernews.parse_thread(HN)
    assert a.company == "Acme Robotics" and "REMOTE (EU)" in a.location and a.match_full_text
    assert a.url.endswith("id=1") and "FastAPI" in a.description
    assert b.company == "Globex" and "US only" in b.location


def test_real_hackernews_headers():
    # Header lines from the November 2026 thread.
    from radar.eligibility import classify
    from radar.models import Job
    c = lambda h: classify(Job("hackernews", "1", "c", h, "u", location=h, remote=True, match_full_text=True))[0]
    assert c("Simple Analytics | DevOps Engineer | REMOTE (worldwide, any timezone) | Full-time, 32h") == "worldwide"
    assert c("Synteq | Growth Lead & Systems Administrator | Austin, TX | REMOTE (US) | Full-time") == "us_only"
    assert c("MixRank (YC S11) | Software Engineers | 100% Remote (Global) | Full-Time") == "worldwide"
    assert c("ORFF Maestra | Prototype Developer | REMOTE (US only) | Contract, part-time") == "us_only"
    assert c("Lyra | Full-Time | Applied AI Engineer | REMOTE (US Citizenship Required)") == "us_only"
    assert c("Mastra | Founding Sales (Founding AE) | US Remote | Full-time") == "us_only"
    assert c("Juro | AI Engineer (Agentic team) | London, UK | REMOTE (UK) | Full-time") == "eu_country"
    assert c("Better Stack | Full-stack Engineer | Remote (North America & Europe)") == "europe"
    assert c("We The Flywheel | Remote | Brand Identity Designer (AI-agent team) | Hourly") == "unclear"
