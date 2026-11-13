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

1. **Collect.** Public job-board APIs of Greenhouse, Lever, Ashby and SmartRecruiters for the ~600 companies in
   `data/companies.json` (including Finnish and Nordic employers such as Wolt, Supercell, ICEYE, Oura, RELEX,
   Reaktor and Solita), plus Himalayas, Remote OK, Remotive, We Work Remotely, Jobicy, Arbeitnow, Working Nomads
   and the monthly Hacker News "Who is hiring?" thread.
2. **Match role.** Job titles are matched against the keyword lists in `config.yaml`, on whole words only.
3. **Eligibility.** `radar/eligibility.py` labels each job `finland`, `europe`, `worldwide`, `eu_country`,
   `us_only`, `other_region` or `unclear`, from the location text first and from phrases in the description
   second ("must be based in the US", "EMEA", "work from anywhere").
4. **Score (0-100).** Role weight, skills found in the posting, seniority, years of experience asked for,
   eligibility, contract or part-time wording, and how recent the posting is.
5. **Dedupe.** The same job on several sites is kept once, preferring the company's own posting.

## Set up (once)

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

## The AI layer

Three parts, each measurable:

**1. LLM classifier** (`radar/llm.py`). After the rules, Claude (Haiku 5.5) reads every job they kept and
returns structured JSON: where it can be done from, employee or contractor, required languages, minimum years,
a one-line summary. Two guards:

- *Evidence check.* The model must quote the sentence it relied on. If the quote is not in the posting word for
  word, the answer is rejected and the rule label stands. This turns a free-text judgement into something
  checkable and catches invented justifications.
- *Cache and budget.* Answers are cached by a hash of the posting (`data/llm_cache.json`), so each posting is
  read once; `max_calls_per_run` caps spend per run. Without an API key the radar runs on rules alone.

**2. Evaluation** (`radar/evaluate.py`). Hand-labelled postings are the ground truth:

```bash
python -m radar sample     # balanced sample of live postings          -> eval/sample.jsonl
python -m radar label      # label them in the terminal (about 30 min) -> eval/labels.jsonl
python -m radar eval       # rules vs LLM vs combined                  -> eval/report.md
```

The report has accuracy, macro F1, per-label precision and recall, confusion matrices, cost, and the number that
matters to a job seeker: of the jobs shown as open to you, how many really are, and how many open jobs are missed.

**3. RAG CV tailoring** (`radar/tailor.py`, runs on your computer only):

```bash
pip install -r requirements-ai.txt
python -m radar tailor --cv private/cv.pdf --job-id "ashby:company:123"   # then paste the posting
```

The CV is chunked by bullet and section; the posting is split into requirement sentences; for each requirement
the best CV chunks are retrieved with BM25 fused with dense embeddings (fastembed, `bge-small-en-v1.5`, reciprocal
rank fusion). Claude writes a summary, bullets and cover note from those chunks only, citing chunk ids, and lists
requirements the CV does not support as gaps. A grounding check then flags any claim that cites nothing, cites a
chunk that was not retrieved, or contains a number not found in the chunks it cites. `private/` and `tailored/`
are git-ignored, so the CV never enters the repository.

### Turn it on

1. Get an API key at console.anthropic.com and add a few dollars of credit.
2. In the repository: Settings -> Secrets and variables -> Actions -> New repository secret, name
   `ANTHROPIC_API_KEY`.
3. For local commands, set the same variable in your terminal (`$env:ANTHROPIC_API_KEY="..."` in PowerShell).

Cost at Haiku 5.5 list prices ($0.10 / $0.50 per million input / output tokens): about $0.30 per 1,000 postings
read, so roughly $0.50 to read the first backlog and cents per day after that. A tailoring run with Sonnet 5.5
costs about one or two cents.

## Finnish and European portals

| Portal | How it is covered | Why |
|---|---|---|
| Jobicy, Arbeitnow, Working Nomads | Collected daily | Free public APIs that allow reuse with a link back |
| Finnish and Nordic employers' own career pages | Collected daily | Public job-board APIs |
| Duunitori | Search link on the dashboard | Its `robots.txt` disallows automated access |
| Oikotie, LinkedIn | Search link on the dashboard | No public feed; automated collection is against their terms |
| Työmarkkinatori (Job Market Finland) | Search link for now | Has an official free interface, but it needs an activation form with a business ID. Once you have credentials it can be added as a collected source. |

The search links are set in `portal_searches` in `config.yaml`; each role's `search` term fills the `{q}` placeholder.

Postings written in Finnish or German are tagged and lose `language_penalty` points, since they usually need
that language. Finnish job titles (ohjelmistokehittäjä, tekoäly...) are matched to roles.

## Limits

- Without an API key, eligibility is rule-based. `unclear` means the posting does not say where you can work
  from; many of those turn out to be US-only, so read the posting before applying.
- Roles are matched on titles, so unusual titles are missed.
- The tracker is stored in your browser. Use *Download my tracker* on the dashboard to back it up.
- It never applies for you. Applications go through each company's own form.

## Good citizenship

Only public, documented job-board endpoints and feeds. The collector identifies itself, runs once a day, uses
8 concurrent requests and backs off when rate-limited. Jobs only, never people. Remote OK, Remotive and
Himalayas, Jobicy and Arbeitnow ask for a link back when their listings are shown; every job links to its original posting.

## Credit

The starting company list was derived from the *US Job Market Engine* dataset by SalesUp (CC BY 4.0), filtered
to companies that had remote roles open to Europe.
