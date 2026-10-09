#!/usr/bin/env python3
"""Job scout: scan public job boards, score against your profile, email the matches.

  python run.py scan                 full scan, score, email the digest
  python run.py scan --dry-run       everything except the email (writes out/digest.html)
  python run.py scan --no-llm        rules only, no API calls
  python run.py discover "Acme,Globex"   find which ATS a company uses
  python run.py test-email           send a sample digest to prove SMTP works
  python run.py stats                what the agent has seen and sent
"""
import argparse
import json
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jobagent import digest, discover, llm, mailer, notify, score  # noqa: E402
from jobagent.sources import adzuna, ats, boards  # noqa: E402
from jobagent.store import Store  # noqa: E402

CONFIG = os.path.join(HERE, "config")
PROFILE_PATH = os.path.join(CONFIG, "profile.json")
COMPANIES_PATH = os.path.join(CONFIG, "companies.json")
DB_PATH = os.path.join(HERE, "out", "jobs.db")
HTML_OUT = os.path.join(HERE, "out", "digest.html")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if default is None:
            sys.exit(f"Missing config file: {path}\n"
                     f"Run 'python run.py init' to create one.")
        return default


def collect(profile, companies, use_boards=True):
    """Fetch from every configured source. Returns (jobs, source_count)."""
    jobs, sources = [], 0
    if companies:
        print(f"  watchlist: {len(companies)} companies")
        jobs += ats.fetch_watchlist(companies, profile)
        sources += 1
    if use_boards:
        for name, fn in (("remotive", boards.remotive), ("remoteok", boards.remoteok),
                         ("himalayas", boards.himalayas)):
            found = fn(profile)
            print(f"  {name}: {len(found)} in-function postings")
            jobs += found
            sources += 1
        found = adzuna.search(profile)
        if found:
            print(f"  adzuna: {len(found)} postings")
            jobs += found
            sources += 1
    return jobs, sources


def load_postings(path):
    """Postings from a JSON file instead of the internet: for demos, evals and replays.

    Each entry takes Job's fields. "days_ago" stands in for "posted" so a
    sample file stays fresh no matter when it runs.
    """
    from dataclasses import fields
    from datetime import datetime, timedelta, timezone
    from jobagent.model import Job
    names = {f.name for f in fields(Job)}
    jobs = []
    for d in load(path)["postings"]:
        d = dict(d)
        if "days_ago" in d:
            d["posted"] = (datetime.now(timezone.utc) - timedelta(days=d.pop("days_ago"))).date().isoformat()
        jobs.append(Job(**{k: v for k, v in d.items() if k in names}))
    return jobs


def dedupe(raw):
    unique, seen = [], {}
    for job in raw:
        if not job.url:
            continue
        first = seen.get(job.fingerprint)
        if first is not None:
            # Same role, another city. Fold the location in rather than repeating it.
            if job.location and job.location.lower() not in first.location.lower():
                first.location = f"{first.location} / {job.location}"[:120]
            continue
        seen[job.fingerprint] = job
        unique.append(job)
    return unique


def hold_back_unreviewed(kept):
    # Only the shortlist reaches the model. On a large watchlist the rest would
    # otherwise claim "strong" on rule score alone, having never been read.
    demoted = 0
    for job in kept:
        if job.tier == "strong" and not job.reviewed:
            job.tier = "look"
            demoted += 1
    return demoted


def rank(fresh, profile, use_llm=True, model=None, explain=False, verbose=True):
    """Rules, then the model on the shortlist. Shared by scan and eval so they cannot drift.

    Returns (scored, kept): every posting with its score and tier, and the ones
    that survived the hard rejects.
    """
    scored = [score.score_job(j, profile) for j in fresh]
    if explain:
        for j in sorted(scored, key=lambda x: -x.score):
            print(f"    [{j.score:>3}] {j.tier:<6} {j.title[:44]:<44} | {j.company[:16]:<16} "
                  f"| {(j.location or '?')[:22]:<22} | {'; '.join(j.reasons)[:70]}")
    kept = [j for j in scored if j.reject != "hard"]
    floor = profile["thresholds"]["llm_floor"]
    shortlist = sorted([j for j in scored if j.score >= floor], key=lambda j: -j.score)[:25]
    if verbose:
        print(f"  {len(kept)} cleared the rules, {len(shortlist)} going to the model")

    reviewed_ran = False
    if shortlist and use_llm:
        reviewed_ran = llm.rerank(shortlist, profile, model=model, verbose=verbose)

    if reviewed_ran:
        demoted = hold_back_unreviewed(kept)
        if demoted and verbose:
            print(f"    {demoted} unreviewed roles held back from strong")
    return scored, kept


def cmd_scan(args):
    profile = load(args.profile or PROFILE_PATH)
    companies = load(COMPANIES_PATH, {"companies": []}).get("companies", [])
    store = Store(args.db or DB_PATH)
    html_out = args.out or HTML_OUT

    if args.postings:
        print(f"Reading postings from {args.postings} (no network)...")
        raw, source_count = load_postings(args.postings), 1
    else:
        print("Scanning...")
        raw, source_count = collect(profile, companies, use_boards=not args.no_boards)
    print(f"  {len(raw)} postings fetched\n")

    # Dedupe within this run, then against everything already seen.
    unique = dedupe(raw)
    fresh = [j for j in unique if store.is_new(j)]
    print(f"Scoring: {len(unique)} unique, {len(fresh)} not seen before")

    scored, kept = rank(fresh, profile, use_llm=not args.no_llm, model=args.model, explain=args.explain)
    ranked = sorted(kept, key=lambda j: -j.score)
    strong = [j for j in ranked if j.tier == "strong"]
    look = [j for j in ranked if j.tier == "look"][:args.max_look]
    promoted = {id(j) for j in strong + look}
    rest = [j for j in ranked if id(j) not in promoted][:args.max_rest]
    print(f"  {len(strong)} strong, {len(look)} worth a look, {len(rest)} ranked below\n")

    hard_drops = [j for j in scored if j.reject == "hard"]
    stats = {"fetched": len(raw), "sources": source_count,
             "cut_line": digest.cut_summary(hard_drops)}
    html = digest.build_html(strong, look, rest, stats, profile)
    text = digest.build_text(strong, look, rest)
    subject = digest.subject(strong, look, rest)

    os.makedirs(os.path.dirname(os.path.abspath(html_out)), exist_ok=True)
    with open(html_out, "w", encoding="utf-8") as fh:
        fh.write(html)

    if args.dry_run:
        print(f"Dry run. Digest written to {html_out}")
        print(f"Subject would be: {subject}")
        if args.open:
            webbrowser.open("file://" + os.path.abspath(html_out).replace("\\", "/"))
        return

    if not (strong or look or rest) and args.skip_empty:
        print("Nothing new. No email sent (--skip-empty).")
        store.log_run(len(raw), len(kept), 0, "empty, suppressed")
        return

    recipients = mailer.send(subject, html, text)
    emailed = strong + look + rest
    for job in emailed:
        store.record(job, sent=True)
    shown = set(id(j) for j in emailed)
    for job in kept:
        if id(job) not in shown:
            store.record(job, sent=False)
    store.log_run(len(raw), len(kept), len(emailed))
    print(f"Emailed {len(emailed)} roles to {', '.join(recipients)}")

    if notify.configured():
        if notify.send(notify.digest_delivered(strong, look, rest, recipients, stats)):
            print("    telegram push sent")


def cmd_eval(args):
    """Score rules-only and rules+model against the labelled sample postings."""
    from jobagent import evaluate
    out_dir = os.path.join(HERE, "eval", "scout")
    os.makedirs(out_dir, exist_ok=True)

    if args.report:
        # Metrics are recomputed from each run's saved tiers, so changing how a
        # metric is defined never needs another API call. Cost is carried over.
        results = []
        for name in sorted(os.listdir(out_dir)):
            if name.endswith(".json"):
                r = load(os.path.join(out_dir, name))
                postings = load(r["postings"])["postings"]
                r["runs"] = [{**evaluate.summarise(postings, run["tiers"]), "cost_usd": run["cost_usd"],
                              "tiers": run["tiers"]} for run in r["runs"]]
                results.append(r)
        results.sort(key=lambda r: (r["mode"] != "rules", r["label"]))
        text = evaluate.report(results)
        with open(os.path.join(out_dir, "REPORT.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        print(text)
        return

    profile = load(args.profile)
    postings = load(args.postings)["postings"]
    use_llm = not args.no_llm
    model = args.model or os.getenv("JOBAGENT_MODEL", "claude-opus-5")
    label = f"rules + {model}" if use_llm else "rules only"
    runs = []
    for i in range(1 if not use_llm else args.repeats):
        unique = dedupe(load_postings(args.postings))
        llm.last_usage.clear()
        scored, _ = rank(unique, profile, use_llm=use_llm, model=model, verbose=False)
        if use_llm and not llm.last_usage:
            sys.exit("The model pass did not run (no API key, or the call failed). Nothing recorded.")
        res = evaluate.summarise(postings, evaluate.outcome(postings, unique, scored),
                                 dict(llm.last_usage) if use_llm else None)
        res["tiers"] = evaluate.outcome(postings, unique, scored)
        runs.append(res)
        print(f"  run {i + 1}: tier accuracy {res['tier_accuracy']:.0%}, {res['strong_emailed']} strong "
              f"(precision {res['strong_precision']}, recall {res['strong_recall']}), "
              f"false drops {res['false_drops']}, checks {res['checks']}")
    slug = "rules" if not use_llm else model
    with open(os.path.join(out_dir, f"{slug}.json"), "w", encoding="utf-8") as fh:
        json.dump({"label": label, "mode": "rules" if not use_llm else "llm", "model": model if use_llm else None,
                   "profile": args.profile, "postings": args.postings, "runs": runs}, fh, indent=2)
    print(f"  -> eval/scout/{slug}.json")


def cmd_discover(args):
    names = [n.strip() for n in args.companies.split(",") if n.strip()]
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            names += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    print(f"Probing {len(names)} companies against Greenhouse, Lever, and Ashby...")
    merged = discover.resolve(names, existing_path=COMPANIES_PATH)
    with open(COMPANIES_PATH, "w", encoding="utf-8") as fh:
        json.dump({"companies": merged}, fh, indent=2)
    print(f"\nWatchlist now has {len(merged)} companies with public boards.")


def cmd_test_email(args):
    """Send a representative sample. It must show all three sections, or it teaches
    the wrong thing about what a real digest looks like."""
    profile = load(PROFILE_PATH)
    from jobagent.model import Job

    strong = [
        Job(source="ashby", company="Vanta", title="Staff Product Designer, Design Systems",
            url="https://jobs.ashbyhq.com/vanta/example-1",
            location="Remote U.S.", comp_text="$180K - $215K", posted="2026-08-28",
            score=93, tier="strong",
            reasons=["target title (staff product designer)", "remote", "comp $180K - $215K"],
            why="Owns the design system end to end, remote US, and the range clears your floor."),
        Job(source="ashby", company="Vanta", title="Head of Design",
            url="https://jobs.ashbyhq.com/vanta/example-2", location="Remote U.S.",
            posted="2026-08-22", score=81, tier="strong",
            reasons=["target title (head of design)", "remote", "matches: mentor, design critique"],
            why="Design leadership with real org influence, a step up in scope from your current role."),
    ]
    look = [
        Job(source="greenhouse", company="Figma", title="Product Designer, Design Systems",
            url="https://boards.greenhouse.io/example/jobs/1",
            location="San Francisco, CA", posted="2026-08-19", score=64, tier="look",
            reasons=["adjacent title (product designer)", "US-based", "matches: design system"],
            why="Strong systems work, but it reads one level below your current scope."),
    ]
    rest = [
        Job(source="ashby", company="Notion", title="Product Designer",
            url="https://jobs.ashbyhq.com/example/jobs/2", location="San Francisco, California",
            comp_text="$285K - $330K", posted="2026-08-30", score=47, tier="long",
            reasons=["adjacent title (product designer)", "outside your range (San Francisco)"]),
        Job(source="remotive", company="Fundraise Up", title="UX Researcher",
            url="https://remotive.com/example", location="Turkey",
            posted="2026-08-27", score=39, tier="long",
            reasons=["adjacent title (ux researcher)", "outside your range (Turkey)"]),
    ]
    stats = {"fetched": 412, "sources": 4,
             "cut_line": "Hid 54: 52 outside your field, 1 too junior, 1 staffing firm."}
    html = digest.build_html(strong, look, rest, stats, profile)
    text = digest.build_text(strong, look, rest)
    recipients = mailer.send("[test] " + digest.subject(strong, look, rest), html, text, to=args.to)
    print(f"Test email sent to {', '.join(recipients)}")
    print("  Sample shows 2 strong, 1 worth a look, 2 ranked below.")


def cmd_init(args):
    """Set up config/profile.json from a bundled starting point, and .env from the template."""
    import shutil
    profiles_dir = os.path.join(CONFIG, "profiles")
    available = sorted(f[:-5] for f in os.listdir(profiles_dir)) if os.path.isdir(profiles_dir) else []

    if os.path.exists(PROFILE_PATH) and not args.force:
        sys.exit(f"{PROFILE_PATH} already exists. Pass --force to overwrite.")

    if args.profile:
        src = os.path.join(profiles_dir, args.profile + ".json")
        if not os.path.exists(src):
            sys.exit(f"No such starting profile: {args.profile}. Available: {', '.join(available)}")
    else:
        src = os.path.join(CONFIG, "profile.example.json")

    shutil.copy(src, PROFILE_PATH)
    print(f"Wrote {PROFILE_PATH}")
    print(f"  from {os.path.basename(src)}")

    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        shutil.copy(os.path.join(HERE, ".env.example"), env_path)
        try:
            os.chmod(env_path, 0o600)
        except Exception:
            pass
        print(f"Wrote {env_path} (fill in SMTP settings)")

    if not os.path.exists(COMPANIES_PATH):
        with open(COMPANIES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"companies": []}, fh, indent=2)
        print(f"Wrote {COMPANIES_PATH} (empty; add with 'run.py discover')")

    print("\nNext:")
    print("  1. Edit config/profile.json, especially resume_summary and titles")
    print("     or: python run.py profile-from-resume path/to/resume.txt")
    print("  2. Edit .env with your SMTP details")
    print("  3. python run.py discover \"Company A,Company B\"")
    print("  4. python run.py scan --dry-run --open")
    if available:
        print(f"\nStarting profiles available: {', '.join(available)}")


def json_type(value):
    """JSON Schema for one example value. bool before int: True is an int in Python."""
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, list):
        return {"type": "array", "items": {"type": "string"}}
    if isinstance(value, dict):
        # Structured outputs rejects an object without its properties spelled out
        # and additionalProperties: false, so nested objects are built the same way.
        return {"type": "object", "properties": {k: json_type(v) for k, v in value.items()},
                "required": list(value), "additionalProperties": False}
    return {"type": "string"}


def profile_schema(template, keys):
    return {"type": "object", "properties": {k: json_type(template[k]) for k in keys},
            "required": list(keys), "additionalProperties": False}


def cmd_profile_from_resume(args):
    """Draft a profile from a resume using Claude, then write it for review."""
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic first.")
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("Set ANTHROPIC_API_KEY in .env first.")

    with open(args.resume, encoding="utf-8", errors="replace") as fh:
        resume = fh.read()[:20000]
    notes = args.notes or ""
    if args.interview:
        with open(args.interview, encoding="utf-8", errors="replace") as fh:
            notes = (fh.read()[:12000] + "\n" + notes).strip()
    template = load(os.path.join(CONFIG, "profile.example.json"))
    schema_keys = [k for k in template if not k.startswith("_")]

    prompt = f"""Read this resume and produce a job-search profile as JSON.

RESUME
{resume}

SETUP INTERVIEW AND NOTES FROM THE USER (these override anything inferred from the resume)
{notes or "(none given)"}

Return a JSON object with exactly these keys: {", ".join(schema_keys)}

Rules:
- resume_summary: 3-6 sentences, third person, concrete about scope, team size, and domain.
- titles_tier1: the roles this person should target next, lowercase, 10-25 entries.
  Include real title variants, not invented ones.
- titles_tier2: adjacent roles worth seeing, lowercase.
- title_block: functions that share vocabulary with this field but are the wrong job.
- keywords_strong: the actual language of this person's work, as it appears in postings.
- fit_signals / anti_signals: plain bullets briefing a recruiter.
- comp_floor / comp_target: use the user's stated numbers; infer from seniority and market only if none.
- locations_local: lowercase towns of the commutable metro around home_base, plus ", st" for the state.
- Keep locations_allow, locations_block, junior_block, seniority_*, thresholds,
  company_skip_patterns, and max_age_days at sensible defaults for this field.
Output only the JSON object."""

    client = anthropic.Anthropic()
    print("Drafting profile from resume...")
    resp = client.messages.create(
        model=args.model or os.getenv("JOBAGENT_MODEL", "claude-opus-5"),
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": profile_schema(template, schema_keys)}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    profile = json.loads(text)
    profile.setdefault("thresholds", template["thresholds"])

    out = args.out or PROFILE_PATH
    if os.path.exists(out) and not args.force:
        out = out.replace(".json", ".draft.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)
    print(f"Wrote {out}")
    print(f"  {len(profile.get('titles_tier1', []))} target titles, "
          f"comp floor ${profile.get('comp_floor', 0):,}")
    print("Read it before the first scan. The titles and comp floor are worth a human pass.")


def cmd_telegram_setup(args):
    """Find the chat id after the user has messaged their bot once."""
    token = args.token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env first, or pass --token.\n"
                 "Get one by messaging @BotFather on Telegram and sending /newbot.")
    chat_id, name = notify.find_chat_id(token=token)
    if not chat_id:
        sys.exit("No messages found. Send your bot any message in Telegram, then run this again.")
    print(f"Chat id: {chat_id}" + (f"  ({name})" if name else ""))
    print("\nAdd this to .env:")
    print(f"  TELEGRAM_CHAT_ID={chat_id}")
    if notify.send("Job Scout is connected. You will get a push here whenever a digest goes out.",
                   chat_id=chat_id, token=token):
        print("\nSent a test message. Check Telegram.")


def cmd_test_telegram(args):
    if not notify.configured():
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.\n"
                 "Run 'python run.py telegram-setup' to find your chat id.")
    from jobagent.model import Job
    sample = [Job(source="ashby", company="Vanta", title="Staff Product Manager, AI Foundations",
                  url="https://jobs.ashbyhq.com/vanta/example", location="Remote U.S.",
                  comp_text="$180K - $215K", score=92, tier="strong",
                  why="Owns the AI quality and evaluation stack, which is your current scope.")]
    ok = notify.send(notify.digest_delivered(sample, [], [], ["you@example.com"],
                                             {"fetched": 892, "sources": 4}))
    print("Telegram push sent." if ok else "Telegram push failed. Check the token and chat id.")


def cmd_stats(args):
    s = Store(DB_PATH).stats()
    print(f"Tracked postings : {s['tracked']}")
    print(f"Emailed          : {s['emailed']}")
    print(f"Strong matches   : {s['strong']}")
    print(f"Scans run        : {s['runs']}")
    print(f"Last run         : {s['last_run'] or 'never'}")
    rows = Store(DB_PATH).recent(10)
    if rows:
        print("\nMost recently sent:")
        for r in rows:
            print(f"  [{r['score']:>3}] {r['title']} - {r['company']}")


def main():
    mailer.load_dotenv(os.path.join(HERE, ".env"))
    ap = argparse.ArgumentParser(description="Job search agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="fetch, score, and email")
    s.add_argument("--dry-run", action="store_true", help="write the digest, send nothing")
    s.add_argument("--open", action="store_true", help="open the digest in a browser (with --dry-run)")
    s.add_argument("--no-llm", action="store_true", help="rules only, no API call")
    s.add_argument("--no-boards", action="store_true", help="watchlist companies only")
    s.add_argument("--skip-empty", action="store_true", help="send nothing when there are no matches")
    s.add_argument("--max-look", type=int, default=8, help="cap on the 'worth a look' section")
    s.add_argument("--max-rest", type=int, default=30, help="cap on the ranked tail")
    s.add_argument("--model", default=None, help="override the Claude model")
    s.add_argument("--explain", action="store_true", help="print every scored posting and why")
    s.add_argument("--profile", help="profile JSON to use (default config/profile.json)")
    s.add_argument("--postings", help="read postings from a JSON file instead of the internet")
    s.add_argument("--db", help="history database (default out/jobs.db)")
    s.add_argument("--out", help="where to write the digest HTML (default out/digest.html)")
    s.set_defaults(func=cmd_scan)

    ev = sub.add_parser("eval", help="score the ranking against labelled sample postings")
    ev.add_argument("--profile", default=os.path.join(HERE, "samples", "scout", "profile.json"))
    ev.add_argument("--postings", default=os.path.join(HERE, "samples", "scout", "postings.json"))
    ev.add_argument("--no-llm", action="store_true", help="rules only (deterministic, free)")
    ev.add_argument("--model", default=None, help="Claude model for the re-rank")
    ev.add_argument("--repeats", type=int, default=3, help="model runs to repeat (variance)")
    ev.add_argument("--report", action="store_true", help="render eval/scout/REPORT.md from saved results")
    ev.set_defaults(func=cmd_eval)

    d = sub.add_parser("discover", help="find each company's ATS board")
    d.add_argument("companies", nargs="?", default="", help="comma-separated company names")
    d.add_argument("--file", help="newline-delimited file of company names")
    d.set_defaults(func=cmd_discover)

    t = sub.add_parser("test-email", help="prove SMTP works")
    t.add_argument("--to", default=None)
    t.set_defaults(func=cmd_test_email)

    i = sub.add_parser("init", help="create profile.json and .env from templates")
    i.add_argument("--profile", help="start from a bundled profile (see config/profiles/)")
    i.add_argument("--force", action="store_true", help="overwrite an existing profile.json")
    i.set_defaults(func=cmd_init)

    r = sub.add_parser("profile-from-resume", help="draft a profile from a resume with Claude")
    r.add_argument("resume", help="path to a .txt or .md resume")
    r.add_argument("--notes", help="extra context: comp floor, location, what you want next")
    r.add_argument("--interview", help="a file of setup-interview answers (see samples/scout/interview.md)")
    r.add_argument("--out", help="where to write (default config/profile.json)")
    r.add_argument("--force", action="store_true", help="overwrite an existing profile.json")
    r.add_argument("--model", default=None)
    r.set_defaults(func=cmd_profile_from_resume)

    tg = sub.add_parser("telegram-setup", help="find your Telegram chat id")
    tg.add_argument("--token", default=None, help="bot token, if not yet in .env")
    tg.set_defaults(func=cmd_telegram_setup)

    tt = sub.add_parser("test-telegram", help="send a sample push")
    tt.set_defaults(func=cmd_test_telegram)

    st = sub.add_parser("stats", help="what the agent has seen")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
