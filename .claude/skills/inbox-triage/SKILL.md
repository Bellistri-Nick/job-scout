---
name: inbox-triage
description: Classify emails or messages into a typed record with a triage skill spec (job_inbox, ap_inbox, or a new one), and build a new spec for another team's inbox. Use when asked to triage, classify, or extract fields from an email, to "run the AP skill on this", to set up triage for a new shared inbox (recruiting, AP, deal desk, support), or to check whether a spec is good enough to turn on.
---

# Inbox triage

One engine (`jobmail/jobmail/triage.py`) runs any skill spec in `jobmail/jobmail/skills/*.json`. A spec is data, not code: the instructions Claude follows, the JSON Schema it must answer in, invariants that code enforces afterwards, and a fallback that pages a human when anything fails.

Setup, once: `cd jobmail && pip install -e ".[dev]"` and an `ANTHROPIC_API_KEY` in the environment or a `.env` file.

## Run a spec on one message

```bash
cd jobmail
python -m jobmail.triage job_inbox path/to/message.eml --env ../.env
python -m jobmail.triage ap_inbox path/to/invoice-email.eml --model claude-sonnet-5-5
```

Prints the record as JSON. Exit code 1 means the fallback was used: say so, and show the error line.

When the user pastes an email instead of a file, save it to a temporary `.txt` file and pass that path. Never paste a real person's email into a sample file or commit it.

## Build a spec for a new inbox

Do these in order. Do not skip step 1: no map, no spec.

1. **Map the current workflow first.** Ask who reads the inbox today, what they decide per message, where the decision goes (ERP, ATS, CRM, a spreadsheet), and what goes wrong when they miss one. The fields in the schema are the decisions they already make. If no one can name the decision, there is nothing to automate yet.
2. **Copy the closest spec** (`ap_inbox.json` for anything with money, `job_inbox.json` for anything with people) to `skills/<name>.json`.
3. **Write the schema.** Every object needs `additionalProperties: false` and every property listed in `required` (structured outputs rejects anything else; `tests/test_triage.py` checks this). Prefer enums to free text for anything code branches on. Use empty strings, not nulls, for unknowns.
4. **Write the instructions.** Define every enum value in the instructions themselves. A definition that lives in a code comment is invisible to the model (this is the bug the job_inbox eval found).
5. **Add invariants for anything safety-critical.** If a field must be true whenever a flag is present (payment-detail change requires human verification), put it in `invariants`. The instructions ask the model; the invariant guarantees it.
6. **Write the fallback so failure is loud.** It must route the message to a person (`needs_reply: true`, `urgency: high`, or the equivalent). A fallback that says "nothing to do" turns an outage into silence.
7. **Label 10 to 25 samples** in `samples/<name>.json`, synthetic or fully anonymised, with an `expect` block each. Include the hard cases on purpose: lookalike senders, ambiguous types, injection attempts, HTML-only mail.
8. **Run the eval and read the misses before anything else.**

```bash
python -m jobmail.evaluate <name> --model claude-sonnet-5-5 --repeats 3 --tag v1 --env ../.env
python -m jobmail.evaluate --report     # writes eval/REPORT.md
```

Register the samples file in `SAMPLES` in `evaluate.py` first. For each miss, decide whether the label, the instructions, or the schema is wrong, fix that one thing, bump the tag, and rerun. Keep the old result file: the before and after is the evidence.

## Rollout gate

A spec is ready for a live inbox when, across three repeats:

- recall on the critical field (the one a miss costs money or an opportunity) is 100% on the labelled set,
- zero fallbacks,
- every miss in `eval/REPORT.md` has a written explanation,
- the owner of the inbox has read ten real outputs side by side with the emails and signed off.

Start read-only: classify and notify, never send, pay, or update a system of record. Widen the scope only after a few weeks of measured accuracy on live traffic.
