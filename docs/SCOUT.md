# Job Scout

A self-hosted job search agent. It scans public job boards every morning, scores what it finds
against your resume, and emails you a ranked digest. The best matches sit at the top, the rest
of your field below, and no role arrives twice. Anything filtered out is counted in the footer.

Runs on a Raspberry Pi, an old laptop, or any machine that stays on. The scan itself is pure
Python standard library. The only optional dependency is the Anthropic SDK, for the pass that
reads postings and judges fit.

Job Scout is the first of two agents. [jobmail](../jobmail/README.md) picks up after you apply, and a
read-only bridge joins the two. How they were built and evaluated, and what the evals found, is in
[CASE_STUDY.md](../CASE_STUDY.md).

## Why this instead of job alerts

Job board alerts match on keywords. Scout scores each posting against your background, then
explains the score. A posting that says "editor" gets checked for whether it means editorial leadership or
video editing. A role 200 miles away still shows up, ranked low, with the reason attached,
because you should decide that, not a filter.

## What it scans

| Source | Coverage | Cost |
|---|---|---|
| Greenhouse, Lever, Ashby | Any company you add to the watchlist | Free, public APIs |
| Remotive | Remote roles, keyword-queried | Free |
| RemoteOK | Remote roles | Free |
| Himalayas | Remote roles, expiry-checked | Free |
| Adzuna | Broad market, including local and agency roles | Free with a dev key |

The ATS endpoints are the same public JSON that powers each company's own careers page. No
scraping, no login, no browser automation. LinkedIn and Indeed are excluded on purpose: both
block automated access, and their native alerts already work.

## Install as a Claude Code skill

Clone it into your skills directory and Claude can drive the whole thing conversationally,
from building your profile to explaining why a role scored the way it did:

```bash
git clone https://github.com/Bellistri-Nick/job-scout ~/.claude/skills/job-scout
```

Then ask: "set up my job search," "scan for jobs," or "why didn't I see any editor roles this
week." The `SKILL.md` in this repo tells Claude how to run it. Setup is an interview: Claude reads
your resume, asks up to seven questions one at a time, saves your answers to `config/interview.md`,
drafts the profile, and reads it back with `run.py profile-check` before anything runs.

It also works as a plain CLI with no Claude involvement. Everything below applies either way.

## Quick start

```bash
git clone <this repo> job-scout && cd job-scout
python run.py init                      # or: --profile ux-designer
```

Then tell it who you are. Write your answers to the setup questions in `config/interview.md`
(see `samples/scout/interview.md` for the shape), let Claude draft the profile, and read it back:

```bash
python run.py profile-from-resume resume.txt --interview config/interview.md   # resume optional
python run.py profile-check
```

Add companies you care about. It verifies each board exists before adding it:

```bash
python run.py discover "Figma,Notion,Vanta,Substack"
```

See what it finds, without sending anything:

```bash
python run.py scan --dry-run --open
```

When the results look right, put your SMTP details in `.env` and run `python run.py scan`.

## Setting up email

Any SMTP server works. Gmail is the path of least resistance:

1. Create a **throwaway** Gmail account. Do not use one that matters.
2. Turn on 2-Step Verification, then create an App Password at
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Put it in `.env` as `SMTP_PASSWORD`, with `MAIL_TO` set to where you want the digest.
4. `python run.py test-email` to confirm delivery.

An App Password grants full mailbox access and cannot be scoped. That is exactly why the
sending account should be one you would not mind losing.

## Running it on a schedule

```bash
scp -r job-scout user@yourpi.local:~/
ssh user@yourpi.local 'bash ~/job-scout/install/install-pi.sh'
```

That builds a venv, locks `.env` to mode 600, and installs a systemd timer for weekday
mornings at 7:15 with a randomized delay, so the boards do not see a robot arriving at the
same second every day. `install/crontab.example` covers non-systemd machines.

## Telegram push (optional)

Get a push the moment a digest is delivered, with the top match and a link, so you know
whether to open the email now or at lunch:

1. Message [@BotFather](https://t.me/botfather) on Telegram, send `/newbot`, copy the token
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN`, then send your new bot any message
3. `python run.py telegram-setup` finds your chat id and sends a test

`python run.py test-telegram` sends a sample push any time. With the two values unset the
feature is a silent no-op, and a push failure never breaks a scan: the email already went.

## How scoring works

Two passes. Rules first, then judgment.

**Rules** (`jobagent/score.py`) are cheap and deterministic. They hard-drop only the
wrong job: out of function, too junior, or a staffing firm reposting the same
listing. Everything else is scored 0-100 on title fit, seniority, location, comp, and how much
of your vocabulary appears in the posting.

Wrong location, comp below floor, and a stale posting date are penalties, not deletions.

**Claude** (`jobagent/llm.py`) then re-ranks the shortlist and writes the one-line "why this
fits" in the email. Only postings above the rule floor reach this pass, so a typical day is a
single small API call, a few cents. Rules hold the floor at 40 percent weight, the model moves
it at 60. Without an API key the agent still runs on rules alone.

Title matching is word-boundary aware, so "intern" does not fire on "internal communications"
and "gis" does not fire on "strategist". Both were real bugs found during calibration.

## The email

Three sections:

1. **Strong match:** full cards with the model's read on why it fits
2. **Worth a look:** the same cards, for roles the score is less sure about
3. **Everything else, ranked:** compact rows, each showing what holds the role back

The footer tallies what was filtered out entirely: `Hid 54: 52 outside your field, 1 too
junior, 1 staffing firm.`

## After you apply: jobmail and the metrics dashboard

Job Scout finds roles. `jobmail/` tracks what happens once you apply, with nothing logged
by hand. It polls your job-search mailbox over IMAP, has Claude classify each message
(company, role, stage, whether you owe a reply), links it to an application, and moves the
stage forward on its own: `applied → screening → interviewing → assessment → offer`, or
`rejected`.

It serves a private, read-only dashboard:

- **Pipeline**: every application by stage, with what needs a reply pinned to the top
- **Metrics** (`/metrics`): response rate, median days to a reply, a funnel, applications per
  month, and which ATS each came through. The funnel reads how far each application *ever*
  got from the event log, so a rejection does not erase the interviews before it
- **API**: `/api/summary` for counts and last poll time, `/health` for monitoring

Alerts go to Telegram or email when something needs you. It also mirrors each application
into an Obsidian note if you use one. It never sends mail on your behalf.

The two are separate installs that share nothing but a Pi. jobmail needs FastAPI and the
Anthropic SDK, so it keeps its own venv. Setup, configuration, and the backfill for
applications from before you installed it are in [jobmail/docs/OPERATIONS.md](../jobmail/docs/OPERATIONS.md).
What it is, how it is evaluated, and the design trade-offs are in [jobmail/README.md](../jobmail/README.md).

```bash
cd jobmail && sudo bash deploy/install.sh $USER
```

## Commands

| Command | What it does |
|---|---|
| `run.py init [--profile NAME]` | Create `profile.json` and `.env` from templates |
| `run.py profile-from-resume FILE [--interview FILE]` | Draft a profile from your resume and setup-interview answers with Claude |
| `run.py discover "A,B,C"` | Find which ATS a company uses, add to the watchlist |
| `run.py scan` | Full run: fetch, score, email |
| `run.py scan --dry-run --open` | Build the digest, open it locally, send nothing |
| `run.py scan --explain` | Print every posting with its score and reasoning |
| `run.py scan --no-llm` | Rules only, no API call |
| `run.py test-email` | Send a sample digest to prove SMTP works |
| `run.py stats` | What the agent has seen and sent |
| `run.py scan --postings FILE --profile FILE --db FILE` | Run on saved postings with no network; used by the demo |
| `run.py eval [--no-llm] [--model M]` | Score the ranking against labelled sample postings ([report](../eval/scout/REPORT.md)) |
| `run.py funnel --jobmail-db FILE` | Join what Scout emailed to how far each role got in jobmail |

## Tuning

Everything lives in `config/profile.json`. No code changes needed.

- **Wrong roles getting through**: add the phrase to `title_block`
- **Right roles getting dropped**: add to `titles_tier1` or `titles_tier2`
- **Too much or too little email**: move `thresholds.strong` and `thresholds.look`
- **Staffing spam**: add to `companies_skip` or `company_skip_patterns`
- **Tail length**: `--max-rest` (default 30) and `--max-look` (default 8)

`--explain` is the tool for all of it. It prints the score and full reason chain for every
posting including the dropped ones, so a bad result tells you exactly which rule to change.

Bundled starting profiles live in `config/profiles/`: `product-management` and
`ux-designer`. Both are invented examples, not real people. Use one as a base with
`run.py init --profile NAME`, then replace every field with your own.

## Security

- The sending account should be a throwaway. App Passwords cannot be scoped.
- `.env` is mode 600 and gitignored. Never commit it.
- Job descriptions are untrusted third-party text. They are HTML-escaped before rendering and
  only `https://` links reach your inbox.
- Postings are fed to the model, so a hostile posting could in principle inflate its own score.
  The model has no tools and no filesystem access, so the worst case is one bad recommendation
  in an email that you would catch on opening the posting.
- Set a monthly spend cap on your API key.

## Tests

```bash
python -m unittest discover -s tests -t .
cd jobmail && pip install -e ".[dev]" && pytest     # jobmail's own suite
```

Standard library only, no network, no API key. The suite pins down the parts that
fail silently: scoring weights and hard rejects, word-boundary matching ("intern" must
not fire on "internal"), salary parsing, and dedupe both within a run (one role posted
in five cities is one job) and across runs (a retitled repost is not a new role). A
change to the scoring rules should move a test, on purpose.

## Layout

```
run.py                        CLI
config/profile.example.json   annotated template
config/profiles/              ready-made starting profiles
config/companies.json         your verified ATS watchlist
jobagent/score.py             rule scoring
jobagent/llm.py               model re-rank, prompt built from your profile
jobagent/digest.py            the email
jobagent/store.py             SQLite dedupe and send history
jobagent/sources/             one module per board family
tests/                        unit tests for scoring, parsing, and dedupe
out/jobs.db                   everything it has ever seen
jobmail/                      application tracker + metrics dashboard (own README)
```

## License

MIT. See LICENSE.
