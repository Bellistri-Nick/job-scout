"""Weekly pipeline brief: evidence from the database, recommendations from Claude.

    python -m jobmail.brief --db demo_out/jobmail.db --out demo_out/brief.md --env ../.env

The brief keeps three things apart, and only one of them comes from a model:

  Evidence         built by code from SQLite. Every fact carries an id.
  Assumptions      the system's own rules, written by code, plus anything
                   the model declares it had to assume.
  Recommendations  written by the pipeline_brief skill. Each must cite
                   evidence ids; code drops citations that do not exist and
                   flags any high-urgency ask no recommendation covers.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config, use_utf8_io
from .db import Database
from .triage import SkillSpec, Triage, TriageResult

FAILED_PREFIX = "Automatic classification failed"


def _days_since(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


# ------------------------------------------------------------------ evidence
def evidence_pack(db: Database, stale_days: int = 14, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    pack: dict[str, Any] = {"as_of": now.date().isoformat(), "open_asks": [], "upcoming_dates": [],
                            "moved_last_7_days": [], "gone_quiet": [], "needs_review": [], "held_for_verification": [],
                            "stats": []}

    for r in db.list_needs_reply():
        pack["open_asks"].append({
            "id": f"M{r['id']}", "application": f"A{r['application_id']}" if r["application_id"] else None,
            "company": r["company"] or "(not named)", "role": r["role"] or "", "stage": r["stage"] or "",
            "urgency": r["urgency"], "ask": r["action_needed"] or r["summary"],
            "from": r["from_name"] or r["from_addr"], "received": (r["sent_at"] or "")[:10],
            "days_waiting": _days_since(r["sent_at"], now),
        })

    for k in db.upcoming_key_dates(within_hours=14 * 24, unalerted_only=False):
        pack["upcoming_dates"].append({
            "id": f"D{k['id']}", "application": f"A{k['application_id']}" if k["application_id"] else None,
            "company": k["company"] or "", "date": k["date"], "what": k["description"] or "",
        })

    since = (now - timedelta(days=7)).isoformat()
    for e in db.conn.execute(
        """SELECT e.application_id, e.detail, e.at, a.company, a.role FROM events e
           JOIN applications a ON a.id = e.application_id
           WHERE e.kind='stage_change' AND e.at >= ? ORDER BY e.at""", (since,)):
        pack["moved_last_7_days"].append({
            "application": f"A{e['application_id']}", "company": e["company"], "role": e["role"] or "",
            "change": e["detail"], "on": e["at"][:10],
        })

    for a in db.stale_applications(stale_days):
        pack["gone_quiet"].append({
            "id": f"A{a['id']}", "company": a["company"], "role": a["role"] or "", "stage": a["stage"],
            "last_activity": (a["last_activity_at"] or "")[:10],
            "days_quiet": _days_since(a["last_activity_at"], now),
        })

    for m in db.conn.execute(
        """SELECT id, from_name, from_addr, subject, summary, message_type, sent_at FROM messages
           WHERE direction='inbound' AND is_job_related=1
             AND COALESCE(message_type, '') <> 'newsletter_or_job_alert'
             AND suspected_fraud = 0
             AND ((application_id IS NULL AND needs_reply=0) OR summary LIKE ?)
             AND sent_at >= ? ORDER BY sent_at""",
        (FAILED_PREFIX + "%", (now - timedelta(days=30)).isoformat())):
        reason = ("classification failed" if (m["summary"] or "").startswith(FAILED_PREFIX)
                  else "job-related but not linked to any application")
        pack["needs_review"].append({
            "id": f"M{m['id']}", "from": m["from_name"] or m["from_addr"], "subject": m["subject"],
            "summary": m["summary"], "reason": reason,
        })

    for m in db.list_held_for_verification():
        if (m["sent_at"] or "") < (now - timedelta(days=30)).isoformat():
            continue
        claimed = json.loads(m["classification"] or "{}").get("company") or ""
        pack["held_for_verification"].append({
            "id": f"H{m['id']}", "from": m["from_addr"], "subject": m["subject"],
            "claims_to_be": claimed, "signals": json.loads(m["fraud_signals"] or "[]"),
            "poses_as_a_company_you_are_talking_to": m["suspected_fraud"] == 2,
        })

    s = db.outcome_stats()
    for i, (name, val) in enumerate([
        ("applications tracked", s["total"]), ("open applications", s["open"]),
        ("applications that got past 'applied'", s["responded"]), ("response rate %", s["response_rate"]),
        ("rejections", s["rejected"]), (f"no response after {s['ghost_days']} days", s["ghosted"]),
        ("median days from applying to first response", s["median_days_to_response"]),
    ], start=1):
        pack["stats"].append({"id": f"S{i}", "name": name, "value": val})
    return pack


def evidence_ids(pack: dict[str, Any]) -> set[str]:
    ids = set()
    for section in ("open_asks", "upcoming_dates", "gone_quiet", "needs_review", "held_for_verification", "stats"):
        ids |= {x["id"] for x in pack[section]}
    ids |= {x["application"] for sec in ("open_asks", "upcoming_dates", "moved_last_7_days")
            for x in pack[sec] if x.get("application")}
    return ids


# ------------------------------------------------------------------ grounding
def ground(result: dict[str, Any], pack: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """Keep only citations that exist. Drop recommendations left with none."""
    valid = evidence_ids(pack)
    kept, notes = [], []
    for rec in result.get("recommendations", []):
        bad = [e for e in rec["evidence"] if e not in valid]
        good = [e for e in rec["evidence"] if e in valid]
        if bad:
            notes.append(f"Dropped citation(s) {', '.join(bad)} from \"{rec['action']}\": not in the evidence.")
        if not good:
            notes.append(f"Removed \"{rec['action']}\": no supporting evidence.")
            continue
        kept.append({**rec, "evidence": good})

    cited = {e for r in kept for e in r["evidence"]}
    for ask in pack["open_asks"]:
        if ask["urgency"] == "high" and ask["id"] not in cited:
            notes.append(f"High-urgency ask {ask['id']} ({ask['company']}: {ask['ask']}) is not covered by any "
                         "recommendation. Check it yourself.")
    return kept, notes


SYSTEM_ASSUMPTIONS = [
    "Message type, urgency and \"needs reply\" are model classifications from the job_inbox skill. "
    "Measured accuracy on the labelled sample set is in eval/REPORT.md; it is not 100%.",
    "An open application is \"gone quiet\" after {stale} days with no mail in either direction.",
    "A newer message on the same application supersedes its older open asks, and any email you send to "
    "that company resolves them. An ask you handled by phone stays open until something newer arrives.",
    "Mail is linked to applications by thread headers first, then company name, then sender domain. "
    "Company and domain links can attach mail to the wrong application when one company has two open roles.",
    "\"Applied\" is the date of the first email seen for that application, not necessarily the day you applied.",
    "A held message is the model's fraud judgment, or a sender domain that imitates one already on file. Held mail "
    "is never linked to an application, so a legitimate company writing from a new domain stays held until you "
    "confirm it.",
]


# ------------------------------------------------------------------ render
def render(pack: dict[str, Any], result: TriageResult | None, kept: list[dict], notes: list[str],
           stale_days: int, model: str) -> str:
    rec = result.record if result else {"pipeline_observation": "", "assumptions": []}
    d = datetime.fromisoformat(pack["as_of"])
    L = [f"# Pipeline brief, {d:%B} {d.day}, {d.year}", ""]
    src = f"Recommendations by {model}" if result else "No model call (--no-llm)"
    L += [f"*{src}. Everything under Evidence and System assumptions comes straight from the tracker "
          f"database; recommendations cite it by id.*", ""]

    L += ["## Recommendations", ""]
    if rec.get("pipeline_observation"):
        L += [f"> {rec['pipeline_observation']}", ""]
    labels = {"now": "Now", "this_week": "This week", "consider": "Worth considering"}
    for pri in ("now", "this_week", "consider"):
        group = [r for r in kept if r["priority"] == pri]
        if not group:
            continue
        L += [f"**{labels[pri]}**", ""]
        for r in group:
            L.append(f"- [ ] {r['action']} {r['why']} *({', '.join(r['evidence'])})*")
        L.append("")
    if not kept:
        L += ["None generated.", ""]

    L += ["## Evidence", ""]
    L += [f"### Open asks ({len(pack['open_asks'])})", ""]
    if pack["open_asks"]:
        L += ["| ID | Company | Stage | The ask | Urgency | Waiting |", "|---|---|---|---|---|---|"]
        for a in pack["open_asks"]:
            L.append(f"| {a['id']} | {a['company']} | {a['stage'] or '-'} | {a['ask']} | {a['urgency']} | "
                     f"{a['days_waiting']}d |")
    else:
        L.append("Nothing waiting on you.")
    L.append("")

    L += [f"### Coming up, next 14 days ({len(pack['upcoming_dates'])})", ""]
    L += [f"- **{d['id']}** {d['date'][:16].replace('T', ' ')}: {d['company']}, {d['what']}" for d in pack["upcoming_dates"]] \
        or ["No dates on file."]
    L.append("")

    L += [f"### Moved in the last 7 days ({len(pack['moved_last_7_days'])})", ""]
    L += [f"- {m['application']} {m['company']}: {m['change']} ({m['on']})" for m in pack["moved_last_7_days"]] \
        or ["No stage changes."]
    L.append("")

    L += [f"### Gone quiet, {stale_days}+ days ({len(pack['gone_quiet'])})", ""]
    L += [f"- **{g['id']}** {g['company']}, {g['role'] or 'role unknown'} ({g['stage']}): last activity "
          f"{g['last_activity']}, {g['days_quiet']} days ago" for g in pack["gone_quiet"]] or ["None."]
    L.append("")

    L += [f"### Needs a human look ({len(pack['needs_review'])})", ""]
    L += [f"- **{n['id']}** from {n['from']}: \"{n['subject']}\". {n['reason'].capitalize()}. "
          f"Model summary: {n['summary']}" for n in pack["needs_review"]] or ["Nothing unlinked or unclassified."]
    L.append("")

    L += [f"### Held for verification ({len(pack['held_for_verification'])})", ""]
    L += [f"- **{h['id']}** from {h['from']}" + (f", claims to be {h['claims_to_be']}" if h["claims_to_be"] else "")
          + f": \"{h['subject']}\". Signals: {', '.join(h['signals'])}."
          + (" **Poses as a company you are in process with.**" if h["poses_as_a_company_you_are_talking_to"] else "")
          for h in pack["held_for_verification"]] or ["Nothing held."]
    L.append("")

    L += ["### Numbers", "", "| ID | Measure | Value |", "|---|---|---|"]
    L += [f"| {s['id']} | {s['name']} | {s['value'] if s['value'] is not None else 'n/a'} |" for s in pack["stats"]]
    L.append("")

    L += ["## Assumptions", "", "**System assumptions** (fixed rules, not model output)", ""]
    L += [f"- {a.format(stale=stale_days)}" for a in SYSTEM_ASSUMPTIONS]
    L.append("")
    if rec.get("assumptions"):
        L += ["**Assumptions the model made for these recommendations**", ""]
        L += [f"- {a}" for a in rec["assumptions"]]
        L.append("")

    L += ["## Validation", ""]
    n_cites = sum(len(r["evidence"]) for r in kept)
    if result and not result.ok:
        L.append(f"- The model call failed ({result.error}). Recommendations are missing; the evidence is complete.")
    elif result:
        L.append(f"- {len(kept)} recommendations, {n_cites} citations, all resolved against the evidence.")
    L += [f"- {n}" for n in notes] or ["- Every high-urgency ask is covered by a recommendation."]
    return "\n".join(L) + "\n"


def build_brief(db: Database, model: str = "claude-opus-5-5", effort: str = "medium", stale_days: int = 14,
                use_llm: bool = True, client: Any = None) -> tuple[str, dict[str, Any]]:
    pack = evidence_pack(db, stale_days)
    result, kept, notes = None, [], []
    if use_llm:
        result = Triage(SkillSpec.load("pipeline_brief"), model=model, effort=effort,
                        max_input_chars=60000, client=client).run(json.dumps(pack, indent=1, default=str))
        kept, notes = ground(result.record, pack)
    return render(pack, result, kept, notes, stale_days, model), pack


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-brief", description="Write the weekly pipeline brief.")
    ap.add_argument("--db", help="SQLite path (default: JOBMAIL_DB_PATH or ~/.jobmail/jobmail.db)")
    ap.add_argument("--out", help="write markdown here (default: print)")
    ap.add_argument("--env", help=".env file holding ANTHROPIC_API_KEY")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--no-llm", action="store_true", help="evidence and assumptions only")
    args = ap.parse_args(argv)
    use_utf8_io()
    cfg = Config.load(args.env)
    db = Database(args.db or cfg.db_path or cfg.data_dir / "jobmail.db")
    if not args.no_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        ap.error("ANTHROPIC_API_KEY not set (use --env, or --no-llm)")
    text, _ = build_brief(db, args.model, args.effort, cfg.stale_after_days, use_llm=not args.no_llm)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
