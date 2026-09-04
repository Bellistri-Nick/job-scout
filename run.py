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

from jobagent import digest, discover, llm, mailer, score  # noqa: E402
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


def cmd_scan(args):
    profile = load(PROFILE_PATH)
    companies = load(COMPANIES_PATH, {"companies": []}).get("companies", [])
    store = Store(DB_PATH)

    print("Scanning...")
    raw, source_count = collect(profile, companies, use_boards=not args.no_boards)
    print(f"  {len(raw)} postings fetched\n")

    # Dedupe within this run, then against everything already seen.
    unique, seen = [], set()
    for job in raw:
        if not job.url or job.fingerprint in seen:
            continue
        seen.add(job.fingerprint)
        unique.append(job)
    fresh = [j for j in unique if store.is_new(j)]
    print(f"Scoring: {len(unique)} unique, {len(fresh)} not seen before")

    scored = [score.score_job(j, profile) for j in fresh]
    if args.explain:
        for j in sorted(scored, key=lambda x: -x.score):
            print(f"    [{j.score:>3}] {j.tier:<6} {j.title[:44]:<44} | {j.company[:16]:<16} "
                  f"| {(j.location or '?')[:22]:<22} | {'; '.join(j.reasons)[:70]}")
    kept = [j for j in scored if j.reject != "hard"]
    floor = profile["thresholds"]["llm_floor"]
    shortlist = sorted([j for j in scored if j.score >= floor], key=lambda j: -j.score)[:25]
    print(f"  {len(kept)} cleared the rules, {len(shortlist)} going to the model")

    if shortlist and not args.no_llm:
        llm.rerank(shortlist, profile, model=args.model)

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

    with open(HTML_OUT, "w", encoding="utf-8") as fh:
        fh.write(html)

    if args.dry_run:
        print(f"Dry run. Digest written to {HTML_OUT}")
        print(f"Subject would be: {subject}")
        if args.open:
            webbrowser.open("file://" + HTML_OUT.replace("\\", "/"))
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
    template = load(os.path.join(CONFIG, "profile.example.json"))
    schema_keys = [k for k in template if not k.startswith("_")]

    prompt = f"""Read this resume and produce a job-search profile as JSON.

RESUME
{resume}

EXTRA CONTEXT FROM THE USER
{args.notes or "(none given)"}

Return a JSON object with exactly these keys: {", ".join(schema_keys)}

Rules:
- resume_summary: 3-6 sentences, third person, concrete about scope, team size, and domain.
- titles_tier1: the roles this person should target next, lowercase, 10-25 entries.
  Include real title variants, not invented ones.
- titles_tier2: adjacent roles worth seeing, lowercase.
- title_block: functions that share vocabulary with this field but are the wrong job.
- keywords_strong: the actual language of this person's work, as it appears in postings.
- fit_signals / anti_signals: plain bullets briefing a recruiter.
- comp_floor / comp_target: infer from seniority and market unless the user stated a number.
- Keep locations_allow, locations_block, junior_block, seniority_*, thresholds,
  company_skip_patterns, and max_age_days at sensible defaults for this field.
Output only the JSON object."""

    client = anthropic.Anthropic()
    print("Drafting profile from resume...")
    resp = client.messages.create(
        model=args.model or os.getenv("JOBAGENT_MODEL", "claude-opus-5"),
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": {
            "type": "object",
            "properties": {k: ({"type": "array", "items": {"type": "string"}}
                               if isinstance(template[k], list) else
                               {"type": "integer"} if isinstance(template[k], int) else
                               {"type": "boolean"} if isinstance(template[k], bool) else
                               {"type": "object"} if isinstance(template[k], dict) else
                               {"type": "string"}) for k in schema_keys},
            "required": schema_keys, "additionalProperties": False}}},
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
    s.set_defaults(func=cmd_scan)

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
    r.add_argument("--out", help="where to write (default config/profile.json)")
    r.add_argument("--force", action="store_true", help="overwrite an existing profile.json")
    r.add_argument("--model", default=None)
    r.set_defaults(func=cmd_profile_from_resume)

    st = sub.add_parser("stats", help="what the agent has seen")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
