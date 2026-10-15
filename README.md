# Job Scout + jobmail

**Two agents that help you find the jobs that fit, track your applications, and keep a search on track.**

- Job Scout reads public job feeds and ranks postings against your background.
- jobmail reads your job-search inbox, links mail to applications, tracks stages, and holds anything that looks like a scam.
- A read-only bridge lets each one use the other's memory. Scout stops surfacing roles you already applied to.

I've run both on a Raspberry Pi against my own search since September 2026. This repo is the public copy. Every sample is synthetic: the resume, profile, and 28 emails belong to John Doe, a fictional candidate, and every company is made up.

## The problem

A job search is a project, and most people run it with a spreadsheet and a pile of email alerts. It breaks in three places.

| The job to do | Where it breaks | Who handles it |
|---|---|---|
| Find roles that match what you need | Board alerts match keywords. "Senior PM, AI" shows up whether the job owns an AI platform or grooms a checkout backlog. Your comp floor and dealbreakers don't make it into the filter | Job Scout |
| Track your applications and your strategy | Fifty applications in parallel means the tracker falls behind the moment you get busy. Stages go stale, and you lose sight of where the search actually stands | jobmail |
| Keep things on track | "Can you send times by Thursday?" sits between job alerts and receipts. Deadlines slip. Fake recruiters impersonate real companies, and a lookalike email looks like routine logistics | jobmail |

The key thing: two of these steps need judgment over free text, and they fail silently. A missed deadline raises no error. A wrong comp floor just buries good roles.

So each agent puts a model on exactly one judgment step: ranking a posting, or reading an email. Everything around it (fetching, deduping, linking, stage tracking, alerting) is plain code that tests can pin down. The current workflow, mapped step by step, is in [CASE_STUDY.md](CASE_STUDY.md#current-workflow-mapped).

Jump to: [Process map](#process-map) · [Run it](#run-it-in-two-minutes) · [What it looks like](#what-it-looks-like) · [Onboarding](#onboarding-setting-it-up-on-your-own-search) · [The lookalike sender](#the-lookalike-sender) · [Human in the loop](#where-a-human-stays-in-the-loop) · [Results and limits](#results-and-limits) · [For reviewers](#what-the-brief-asks-for-and-where-it-is) · [Repo map](#repo-map)

## Process map

Today a search is ten manual steps. Two need judgment (3 and 7). The bookkeeping around them, logging applications, updating stages, and reviewing follow-ups, is what gets skipped on busy days.

![Process map of the manual job search, ten steps, with the two judgment steps highlighted](docs/img/process-before.png)

With the agents, the model makes the two judgment calls: ranking a posting and classifying an email. It also drafts the weekly brief's recommendations, and code checks every citation. Code does the fetching, linking, stage tracking, and alerting. You decide what to apply to, what to reply to, and what to do with a held message.

![Process map of the agent workflow in three lanes: Job Scout, you, and jobmail, with model steps in orange, code in gray, and human steps in blue](docs/img/process-after.png)

The same maps as editable Mermaid, plus the tables of where a human decides and where the model sits, are in [docs/PROCESS_MAP.md](docs/PROCESS_MAP.md).

## Run it in two minutes

```bash
git clone https://github.com/Bellistri-Nick/job-scout && cd job-scout
pip install -e ./jobmail       # Scout needs only the standard library; jobmail needs a few packages
python demo.py                 # the whole workflow, offline: no API key, no network
```

`demo.py` walks seven steps and tells you what to look for in each. Scout scans 22 sample postings. jobmail processes two weeks of sample email (28 messages) by replaying Claude's recorded answers through the real pipeline. Then Scout runs again with jobmail's memory and skips roles you already applied to. With an API key, every step also runs live. The commands are in [CASE_STUDY.md](CASE_STUDY.md) and [jobmail/README.md](jobmail/README.md).

## What it looks like

All three screenshots come from the offline demo, so you can reproduce them.

### The jobmail dashboard

Mail that needs a reply sits at the top, with the action spelled out and a deadline where the email gave one. Below it, the pipeline by stage.

![jobmail dashboard showing four emails that need a reply, then the pipeline by stage](docs/img/dashboard.png)

### The Scout digest

Ranked roles, each with a score, a one-line reason, and the rules that fired. The 22 sample postings come down to the 10 worth your time.

![Scout digest listing strong matches with scores and reasons](docs/img/scout-digest.png)

### The weekly brief

Every recommendation is a checkbox that cites the evidence behind it (the small `M27`, `D5` tags). Code verifies each citation before the brief goes out. This one catches the lookalike Northbeam email and tells you to confirm through the thread you already trust.

![Weekly pipeline brief with recommendations and evidence citations](docs/img/weekly-brief.png)

## Onboarding: setting it up on your own search

Setup is a conversation, and it matters more than any other step. The profile decides every score, so a bad profile fails silently. Job Scout ships as a Claude Code skill ([SKILL.md](SKILL.md)) that interviews you and writes the profile. You never edit JSON.

The flow, in order:

1. Resume first. Drop in a resume. Claude reads it, plays it back in two sentences (level, domain, strongest scope signal), and asks what that misses. Anything the resume already answers becomes a confirmation, not a question.
2. Seven questions at most, one per message. Questions the resume answered get skipped.
3. One follow-up per vague answer. "Senior roles" becomes which titles. "Good pay" becomes a number. After one follow-up, Claude writes down a sensible default, says it did, and moves on.
4. Read-back. Claude saves your answers in your own words to `config/interview.md`, reads them back in a short paragraph, and asks "anything wrong?" That file is the record, so changing your search later means editing one answer, not redoing the interview.
5. Profile check. Claude drafts the profile, then `python run.py profile-check` explains in plain language what it will do: target titles, what never reaches you, where, pay, and how much email to expect. Every warning gets fixed before the first scan. Then one last question: "Is there a role you'd want that this would miss?"
6. Watchlist, email, dry run. You name companies you'd take a call from, and `python run.py discover` keeps only the job boards that resolve. Then you wire up email and run `python run.py scan --dry-run --open`. You review the results together before anything sends.

The questions:

| # | Claude asks | It pushes for | Becomes |
|---|---|---|---|
| 0 | "Walk me through your last two roles: title, team size, what you owned." (only without a resume) | Scope and numbers, not duties | Background, strong keywords |
| 1 | "What's the next role? The title you'd say yes to, and the one that's a step down but still okay." | Real title variants, the level floor | Target titles, seniority |
| 2 | "Where do you need to work? Home base, remote, and how many days in an office you'd accept." | Towns you'd commute to, a hard "no" | Home base, locations |
| 3 | "What's your base salary floor, and what are you aiming for? Is a posting with no salary okay?" | A number, not a range | Comp floor and target |
| 4 | "What should never reach your inbox? Roles, levels, industries, or companies." | Neighbouring functions that share vocabulary | Blocked titles, levels, companies |
| 5 | "Describe a role you'd drop everything for." | The kind of work, scope, and domain | Strong-fit signals |
| 6 | "What does a posting look like that seems right but isn't?" | The trap, in your words | Anti-signals |

A real set of answers is in [samples/scout/interview.md](samples/scout/interview.md), and `python run.py profile-from-resume samples/scout/resume.txt --interview samples/scout/interview.md` turns it into the sample profile.

### Telegram alerts

Both agents can push to Telegram the moment something needs you: Scout when a digest lands, jobmail when a reply or a held scam needs a look. You create the bot yourself, because Claude can't:

1. Message [@BotFather](https://t.me/botfather) on Telegram, send `/newbot`, and copy the token.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`, then send your new bot any message.
3. Run `python run.py telegram-setup` for Scout, or `python -m jobmail.telegram_setup --write` for jobmail. Scout's command finds your chat id, prints it for you to put in `.env`, and sends a test. jobmail's `--write` saves the chat id for you, then sends a test.

With the token unset, Telegram is a silent no-op, and a failed push never breaks a scan. Email still goes out.

### jobmail setup

jobmail has no interview. It learns your search from your mail. You give it a Gmail account with an App Password, an Anthropic API key, and an `OWNER_NAME` so the classifier knows who "you" is. Then `python -m jobmail.preflight` checks the connection and `python -m jobmail.pipeline --dry-run -v` does a first run with no alerts. To recover applications you sent before installing, `python -m jobmail.backfill --list` shows what it would find and spends nothing. The full steps are in [jobmail/docs/OPERATIONS.md](jobmail/docs/OPERATIONS.md).

## What's live

The Pi runs jobmail v2.2, installed October 14, 2026. It classifies mail, links it to applications, tracks stages, sends alerts, serves the dashboard, holds recruiting scams for review, and emails a weekly brief on Friday mornings. It has tracked 93 applications with no manual entry.

Two caveats. The first scheduled brief goes out Friday, October 16. A manual test send worked, but nothing has run unattended yet. And I haven't verified the Scout-to-jobmail bridge on the Pi, so its results still come from the evals and `demo.py`.

## Results and limits

What I've observed, and what I haven't:

- jobmail has tracked 93 applications on the Pi with no manual entry.
- Cost per unit on the eval's production models: about 4¢ per Scout scan, $0.0071 per email jobmail classifies, and about 5¢ per weekly brief.
- 77 Scout tests and 89 jobmail tests, all offline.
- Not measured: time saved. There's no clean before and after, so it's a hypothesis. The test is a two-week time study, minutes per day in the inbox and on job boards, before and after. [CASE_STUDY.md](CASE_STUDY.md#observed-results-vs-expected-benefits) has the metrics I'd track for a team.

Limits worth knowing before you trust it:

- The eval sets are small and synthetic (21 postings, 26 emails, one brief input). The next step is 100+ real labelled items, kept private.
- Every email body goes to the model provider. A team rollout needs a data processing agreement, zero retention where offered, redaction before the call, and scoped OAuth.
- The bridge matches on company and role text. A shared job id from Scout's link to the application would be exact.
- It serves one user, and fixing a wrong link means editing SQL.

What the evals and demos taught me is in [CASE_STUDY.md](CASE_STUDY.md#learnings). The one that changed the design: the model can't judge what it can't see. It passed the lookalike email as genuine because the tell lives in the database, so a dozen lines of code that read that history do the work.

## Time spent

I've been building this for my own search since April. About 3 to 4 hours of my own time went into refining it for this assignment: the skill engine, evals, bridge, brief, fraud handling, and demo, built with Claude Code. Phases and dates are in [CASE_STUDY.md](CASE_STUDY.md#time-spent).

## What the brief asks for, and where it is

| Requirement | Where | In one line |
|---|---|---|
| Agentic workflow, with boundaries | [CASE_STUDY.md](CASE_STUDY.md), [jobmail architecture](jobmail/README.md#architecture) | The model reads and judges. Code links, moves stages, and decides what may page you. Nothing is ever sent to anyone but you |
| Reusable skills | [jobmail skills](jobmail/README.md#reusable-skills), [`.claude/skills/inbox-triage`](.claude/skills/inbox-triage/SKILL.md), [SKILL.md](SKILL.md) | A skill is a JSON spec: instructions, schema, invariants, fallback. One engine runs three of them, including a Finance AP inbox with no new code |
| Context and memory | [Memory](CASE_STUDY.md#memory-across-steps-runs-and-agents) | Profile, seen-postings history, and an application data model with an append-only event log. Demo steps 2, 4, and 5 show memory changing a result |
| Usable output | [digest](examples/scout-digest.html), [weekly brief](jobmail/examples/pipeline-brief.md) | The brief separates evidence (written by code), assumptions (code and model, labelled), and recommendations (model, each citing evidence ids that code verifies) |
| Technical fluency | [demo.py](demo.py), [run it](jobmail/README.md#run-it) | Python CLIs, SQLite, systemd on a Pi, built in Claude Code |
| Evaluation | [Scout eval](eval/scout/REPORT.md), [jobmail eval](jobmail/eval/REPORT.md) | Hand-labelled sets, three repeats per configuration, cost per item, every miss listed. 77 + 89 offline tests |
| Edge cases and failures | [jobmail edge cases](jobmail/README.md#edge-cases-and-failures) | Prompt injection, keyword stuffing, scams, a lookalike sender domain, and a silent outage the eval reproduced |
| User value and measurement | [Results and limits](#results-and-limits), [metrics for a team](CASE_STUDY.md#observed-results-vs-expected-benefits) | Observed results kept apart from expected benefits. A two-week time study to measure time saved, and the metrics I'd track for a team |
| Iterations, obstacles, learnings, time | [Versions](CASE_STUDY.md#versions), [jobmail obstacles](jobmail/README.md#obstacles), [learnings](CASE_STUDY.md#learnings), [time spent](#time-spent) | Five versions from April to Oct 10, the real obstacles, and about 3 to 4 hours of my own time on this assignment |

## The lookalike sender

An email arrives from `marcus.lee@northbeam-careers.example`: "We've moved final rounds to a new portal, please check in before Wednesday." It reads like routine logistics from the recruiter you're already talking to.

The model passed it as genuine in 6 of 6 eval runs, on both the production model and the cheap one. Nothing in the text gives it away. The tell is the sender. Every earlier Northbeam email came from `northbeam.example`, and only jobmail's memory knows that.

So the pipeline checks each sender against the domains already linked to your applications. It holds the lookalike, never links it, never learns its domain, and sends one alert: *Possible scam, verify before acting: claims to be Northbeam.* The weekly brief then recommends confirming through the thread you already trust.

A real background check that asks for an SSN, sent through Checkr after an offer, goes through untouched. That case is in the eval on purpose. If fraud handling gets over-eager, it fails loudly.

## Where a human stays in the loop

| Point | Why |
|---|---|
| Scout's profile is read and approved before the first scan | In testing, the drafted profile loosened the email thresholds without being asked. A wrong comp floor buries good roles silently |
| Held messages are never acted on | jobmail flags, you decide. A real company writing from a new domain stays held until you confirm it |
| "Needs a human look" list | Mail the classifier couldn't link, or a failed classification. The fallback pages you instead of filing the email as handled |
| The brief's recommendations are checkboxes | The model proposes, code checks every citation, you act |
| Outbound mail | The only mail either agent sends is an alert or the weekly brief, to your own address. They never reply to, forward, or contact a recruiter. The inbox is read-only |

## Built vs. reused

Built: both agents, the triage engine and its three skill specs, scoring, the digest, memory and data model, the bridge, the brief, the demos, and both eval harnesses. I built it in Claude Code. I chose the problem and the architecture, wrote the labels, and made the trade-off calls. Claude Code wrote most of the code, and every change went through the tests and evals in this repo.

Reused: the Anthropic Python SDK, public Greenhouse, Lever, Ashby, Workable, and SmartRecruiters job feeds, SQLite, FastAPI, Jinja2, systemd, and Tailscale.

## Repo map

```
README.md                you are here
CASE_STUDY.md            the full write-up: problem, map, versions, evals, trade-offs, learnings, time
demo.py                  the whole workflow, offline
run.py, jobagent/        Job Scout: sources, rules, model re-rank, digest, memory, eval
docs/SCOUT.md            operating Job Scout: install, tuning, scheduling
docs/PROCESS_MAP.md      the process map: before and after, human checkpoints, model vs code
docs/img/                screenshots used in this README
samples/scout/           sample resume, setup interview, profile, 22 postings
eval/scout/              Scout eval results and report
jobmail/                 jobmail: triage engine, skills, pipeline, brief, evals (own README)
.claude/skills/          Claude Code skill for pointing the triage engine at a new team's inbox
SKILL.md                 Claude Code skill for running Job Scout
```

MIT licensed. See [LICENSE](LICENSE).
