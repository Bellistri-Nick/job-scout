# Job Scout + jobmail

Two agents that run one workflow: find the roles worth applying to, then keep track of everything that happens after you apply. Job Scout reads job boards and ranks postings against your background. jobmail reads your job-search inbox, keeps the pipeline current, and holds anything that looks like a recruiting scam. A read-only bridge lets each one use the other's memory.

Both have run on a Raspberry Pi against my own search since September 2026. This repo is the public copy, with synthetic sample data in place of my mail.

## Run it in two minutes

```bash
git clone https://github.com/Bellistri-Nick/job-scout && cd job-scout
pip install -e ./jobmail       # Scout needs only the standard library; jobmail needs a few packages
python demo.py                 # the whole workflow, offline: no API key, no network
```

`demo.py` walks seven steps and says what to look for in each. Scout scans 22 sample postings. jobmail processes two weeks of sample email (28 messages) by replaying Claude's recorded answers through the real pipeline. Then Scout runs again with jobmail's memory and skips roles you already applied to. With an API key, every step also runs live; the commands are in [CASE_STUDY.md](CASE_STUDY.md) and [jobmail/README.md](jobmail/README.md).

## The problem

A senior search runs 50+ applications in parallel. Two steps need judgment over free text, and both fail without any error to warn you:

1. **Is this posting worth applying to?** Board alerts match keywords, so "Senior PM, AI" arrives whether the job owns an AI platform or grooms a checkout backlog.
2. **Does this email need me, and by when?** "Can you send times by Thursday?" sits between job alerts and receipts. The tracker falls behind. Fake recruiters impersonate real companies.

Each agent puts a model on exactly one of those steps. Everything around them (fetching, deduping, linking, stage tracking, alerting) is plain code that tests can pin down. The current workflow, mapped step by step, is in [CASE_STUDY.md](CASE_STUDY.md#current-workflow-mapped).

## What the brief asks for, and where it is

| Requirement | Where | In one line |
|---|---|---|
| Agentic workflow, with boundaries | [CASE_STUDY.md](CASE_STUDY.md), [jobmail architecture](jobmail/README.md#architecture) | Model reads and judges; code links, moves stages, and decides what may page you. Nothing ever sends mail |
| Reusable skills | [jobmail skills](jobmail/README.md#reusable-skills), [`.claude/skills/inbox-triage`](.claude/skills/inbox-triage/SKILL.md), [SKILL.md](SKILL.md) | A skill is a JSON spec: instructions, schema, invariants, fallback. One engine runs three of them, including a Finance AP inbox with no new code |
| Context and memory | [Memory](CASE_STUDY.md#memory-across-steps-runs-and-agents) | Profile, seen-postings history, and an application data model with an append-only event log. Demo steps 2, 4 and 5 show memory changing a result |
| Usable output | [digest](examples/scout-digest.html), [weekly brief](jobmail/examples/pipeline-brief.md) | The brief separates evidence (written by code), assumptions (code and model, labelled) and recommendations (model, each citing evidence ids that code verifies) |
| Technical fluency | [demo.py](demo.py), [run it](jobmail/README.md#run-it) | Python CLIs, SQLite, systemd on a Pi, built in Claude Code |
| Evaluation | [Scout eval](eval/scout/REPORT.md), [jobmail eval](jobmail/eval/REPORT.md) | Hand-labelled sets, three repeats per configuration, cost per item, every miss listed. 54 + 85 offline tests |
| Edge cases and failures | [jobmail edge cases](jobmail/README.md#edge-cases-and-failures) | Prompt injection, keyword stuffing, scams, a lookalike sender domain, and a silent outage the eval reproduced |

## The lookalike sender

An email arrives from `marcus.lee@northbeam-careers.example`: "We've moved final rounds to a new portal, please check in before Wednesday." It reads like routine logistics from the recruiter you're already talking to.

The model passed it as genuine in 6 of 6 eval runs, on both the production model and the cheap one, because nothing in the text gives it away. The tell is the sender: every earlier Northbeam email came from `northbeam.example`, and only jobmail's memory knows that. So the pipeline checks each sender against the domains already linked to your applications, holds the lookalike, never links it, never learns its domain, and sends one alert: *Possible scam, verify before acting: claims to be Northbeam.* The weekly brief then recommends confirming through the thread you already trust.

A real background check that asks for an SSN, sent through Checkr after an offer, goes through untouched. That case is in the eval on purpose, so it fails loudly if fraud handling gets over-eager.

## Where a human stays in the loop

| Point | Why |
|---|---|
| Scout's profile is read and approved before the first scan | In testing, the drafted profile loosened the email thresholds without being asked. A wrong comp floor buries good roles silently |
| Held messages are never acted on | jobmail flags; you decide. A real company writing from a new domain stays held until you confirm it |
| "Needs a human look" list | Mail the classifier couldn't link, or a failed classification. The fallback pages you instead of filing the email as handled |
| The brief's recommendations are checkboxes | The model proposes; code checks every citation; you act |
| Sending mail | Never automated. Both agents are read-only toward the outside world |

## Built vs. reused

**Built:** both agents, the triage engine and its three skill specs, scoring, the digest, memory and data model, the bridge, the brief, the demos, and both eval harnesses. I built it in Claude Code. I chose the problem and the architecture, wrote the labels, and made the trade-off calls. Claude Code wrote most of the code, and every change went through the tests and evals in this repo.

**Reused:** the Anthropic Python SDK, public Greenhouse, Lever and Ashby job feeds, SQLite, FastAPI, Jinja2, systemd and Tailscale.

## Repo map

```
README.md                you are here
CASE_STUDY.md            the full write-up: problem, map, versions, evals, trade-offs, learnings, time
demo.py                  the whole workflow, offline
run.py, jobagent/        Job Scout: sources, rules, model re-rank, digest, memory, eval
docs/SCOUT.md            operating Job Scout: install, tuning, scheduling
samples/scout/           sample resume, setup interview, profile, 22 postings
eval/scout/              Scout eval results and report
jobmail/                 jobmail: triage engine, skills, pipeline, brief, evals (own README)
.claude/skills/          Claude Code skill for pointing the triage engine at a new team's inbox
SKILL.md                 Claude Code skill for running Job Scout
```

MIT licensed. See [LICENSE](LICENSE).
