# Job Scout

A self-hosted job search agent. It scans public job boards every morning, scores what it finds
against your resume, and emails you a ranked digest. Best matches up top, everything else in
your field below it, nothing repeated, and nothing thrown away without being counted.

Runs on a Raspberry Pi, an old laptop, or any machine that stays on. The scan itself is pure
Python standard library. The only optional dependency is the Anthropic SDK, for the pass that
reads postings and judges fit.

## Why this instead of job alerts

Job board alerts match on keywords. This scores against your actual background, then explains
itself. A posting that says "editor" gets checked for whether it means editorial leadership or
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

Then just ask: "set up my job search," "scan for jobs," or "why didn't I see any editor
roles this week." The `SKILL.md` in this repo tells Claude how to run it.

It also works as a plain CLI with no Claude involvement. Everything below applies either way.

## Quick start

```bash
git clone <this repo> job-scout && cd job-scout
python run.py init                      # or: --profile ux-designer
```

Then tell it who you are. Either edit `config/profile.json` by hand, or let Claude draft it:

```bash
python run.py profile-from-resume resume.txt --notes "remote or Boston, floor $130k"
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

## How scoring works

Two passes. Rules first, then judgment.

**Rules** (`jobagent/score.py`) are cheap and deterministic. They hard-drop only what is
genuinely the wrong job: out of function, too junior, or a staffing firm reposting the same
listing. Everything else is scored 0-100 on title fit, seniority, location, comp, and how much
of your actual vocabulary appears in the posting.

Wrong location, comp below floor, and a stale posting date are penalties, not deletions.

**Claude** (`jobagent/llm.py`) then re-ranks the shortlist and writes the one-line "why this
fits" in the email. Only postings above the rule floor reach this pass, so a typical day is a
single small API call, a few cents. Rules hold the floor at 40 percent weight, the model moves
it at 60. Without an API key the agent still runs on rules alone.

Title matching is word-boundary aware, so "intern" does not fire on "internal communications"
and "gis" does not fire on "strategist". Both were real bugs found during calibration.

## The email

Three sections:

1. **Strong match** — full cards with the model's read on why it fits
2. **Worth a look** — same treatment, lower confidence
3. **Everything else, ranked** — compact rows, each showing the catch

The footer tallies what was filtered out entirely: `Hid 54: 52 outside your field, 1 too
junior, 1 staffing firm.` Nothing vanishes silently.

## Commands

| Command | What it does |
|---|---|
| `run.py init [--profile NAME]` | Create `profile.json` and `.env` from templates |
| `run.py profile-from-resume FILE` | Draft a profile from your resume with Claude |
| `run.py discover "A,B,C"` | Find which ATS a company uses, add to the watchlist |
| `run.py scan` | Full run: fetch, score, email |
| `run.py scan --dry-run --open` | Build the digest, open it locally, send nothing |
| `run.py scan --explain` | Print every posting with its score and reasoning |
| `run.py scan --no-llm` | Rules only, no API call |
| `run.py test-email` | Send a sample digest to prove SMTP works |
| `run.py stats` | What the agent has seen and sent |

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
out/jobs.db                   everything it has ever seen
```

## License

MIT. See LICENSE.
