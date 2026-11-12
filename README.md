# Remote Job Radar

Finds remote jobs that someone living in Finland can actually take, ranks them against a target profile,
and tracks applications.

Most jobs labelled "remote" are open to one country only, usually the US. This project reads postings from
company career pages and remote job boards every day, works out **where each job can be done from**, keeps the
ones open to Finland, Europe or worldwide, and scores them against the roles and skills in `config.yaml`.

## What you get

| | |
|---|---|
| **Dashboard** | `docs/index.html`, served by GitHub Pages. Jobs grouped by role, with filters, a match score, the reason each job matched, an **Open posting** button and an application tracker. |
| **Daily digest** | Each morning's new matches arrive as a GitHub issue (GitHub emails it to you) and are saved in `digests/latest.md`. |
| **Data** | `docs/jobs.json` holds the current matches. `data/seen.json` remembers when each job was first seen. |

## How it works

```
collect  ->  match role  ->  where can it be done from?  ->  score  ->  dedupe  ->  dashboard + digest
```

1. **Collect.** Public job-board APIs of Greenhouse, Lever and Ashby for the ~570 companies in
   `data/companies.json`, plus Himalayas, Remote OK, Remotive, We Work Remotely and the monthly Hacker News
   "Who is hiring?" thread.
2. **Match role.** Job titles are matched against the keyword lists in `config.yaml`, on whole words only.
3. **Eligibility.** `radar/eligibility.py` labels each job `finland`, `europe`, `worldwide`, `eu_country`,
   `us_only`, `other_region` or `unclear`, from the location text first and from phrases in the description
   second ("must be based in the US", "EMEA", "work from anywhere").
4. **Score (0-100).** Role weight, skills found in the posting, seniority, years of experience asked for,
   eligibility, contract or part-time wording, and how recent the posting is.
5. **Dedupe.** The same job on several sites is kept once, preferring the company's own posting.

## Set up (once)

0. **Add the schedule:** move `setup/daily.yml` to `.github/workflows/daily.yml` and push.
1. **Run it:** Actions tab -> *Daily job radar* -> *Run workflow*. After that it runs by itself every morning.
2. **Turn on the dashboard:** Settings -> Pages -> *Deploy from a branch* -> branch `main`, folder `/docs`.
   The dashboard appears at `https://<your-user>.github.io/remote-job-radar/`.
   (GitHub Pages is free for public repositories; private ones need a paid plan.)
3. **Get the digest by email:** make sure you are watching the repository (Watch -> All activity).

## Change what it looks for

Everything is in `config.yaml`:

- `roles`: the roles you want, their title keywords and how much you want each (`weight`).
- `profile.skills`: skills that add to the score when a posting mentions them.
- `exclude_title`: words that drop a job (other tech stacks, management, sales).
- `keep_eligibility` and `min_score`: how strict the shortlist is.
- `agency_blocklist`: staffing firms and aggregators to ignore.

Add a company by putting its job-board slug in `data/companies.json`. The slug is the part after the host in
its careers URL, e.g. `jobs.ashbyhq.com/<slug>`, `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`.

## Run locally

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m radar run                                       # full run, a few minutes
python -m radar run --sources hackernews,remotive         # quick partial run
python -m radar run --sources ashby --limit-boards 20     # first 20 Ashby boards only
python -m http.server -d docs                             # dashboard at http://localhost:8000
```

## Limits

- Eligibility is rule-based. `unclear` means the posting does not say where you can work from; many of those
  turn out to be US-only, so read the posting before applying. An LLM reading the full description is the
  planned next step.
- Roles are matched on titles, so unusual titles are missed.
- The tracker is stored in your browser. Use *Download my tracker* on the dashboard to back it up.
- It never applies for you. Applications go through each company's own form.

## Good citizenship

Only public, documented job-board endpoints and feeds. The collector identifies itself, runs once a day, uses
8 concurrent requests and backs off when rate-limited. Jobs only, never people. Remote OK, Remotive and
Himalayas ask for a link back when their listings are shown; every job links to its original posting.

## Credit

The starting company list was derived from the *US Job Market Engine* dataset by SalesUp (CC BY 4.0), filtered
to companies that had remote roles open to Europe.
