"""The whole workflow, end to end, offline: find roles, track them, close the loop.

    pip install -e ./jobmail        # once; Scout itself needs only the standard library
    python demo.py

No API key and no network. Scout runs its rules on 22 sample postings; jobmail
replays Claude's recorded answers for 28 sample emails through the real
pipeline. Everything lands in demo_out/ and jobmail/demo_out/.

Each step prints what to look for. Steps 2, 4 and 5 are where memory changes
a result.
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JOBMAIL = os.path.join(HERE, "jobmail")
OUT = os.path.join(HERE, "demo_out", "scout")
JOBMAIL_DB = os.path.join(JOBMAIL, "demo_out", "jobmail.db")
SCAN = [sys.executable, "run.py", "scan", "--dry-run", "--no-llm",
        "--profile", "samples/scout/profile.json", "--postings", "samples/scout/postings.json"]


def step(n, title, notice):
    print(f"\n{'=' * 78}\n{n}. {title}\n   Look for: {notice}\n{'=' * 78}")


def run(cmd, cwd=HERE, keep=None):
    """Run a step and show its output, or only the lines that matter."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        print(res.stdout, res.stderr)
        sys.exit(f"step failed: {' '.join(cmd)}")
    lines = res.stdout.splitlines()
    if keep:
        lines = [l for l in lines if any(k in l for k in keep)]
    print("\n".join("   " + l for l in lines))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
    except (AttributeError, ValueError):
        pass
    os.makedirs(OUT, exist_ok=True)
    try:
        import jobmail  # noqa: F401
    except ImportError:
        sys.exit("jobmail is not installed in this Python. Run: pip install -e ./jobmail")

    step(1, "Scout scans 22 postings against the profile",
         "rules hard-drop only wrong jobs; 16 roles ranked; the digest is demo_out/scout/digest.html")
    run(SCAN + ["--db", os.path.join(OUT, "jobs.db"), "--reset", "--record-sent",
                "--out", os.path.join(OUT, "digest.html")])

    step(2, "The next morning, the same postings",
         "memory: all 16 roles in your field were seen yesterday, so nothing is emailed twice. The 5 "
         "'not seen' are wrong-function postings, never stored, rejected again on title alone")
    run(SCAN + ["--db", os.path.join(OUT, "jobs.db"), "--out", os.path.join(OUT, "digest-day2.html")],
        keep=["Scoring", "strong", "Subject"])

    step(3, "jobmail, week 1: 12 emails after applying",
         "auto-acks open applications; the recruiter's ask becomes a dated action")
    run([sys.executable, "-m", "jobmail.demo", "--run", "1", "--reset", "--replay"], cwd=JOBMAIL)

    step(4, "jobmail, week 2: 14 more emails, same database",
         "replies thread onto week-1 applications; your sent mail clears asks; "
         "the email from northbeam-careers.example is held because memory says Northbeam writes from "
         "northbeam.example, and the model alone passed it as real in 6 of 6 eval runs")
    run([sys.executable, "-m", "jobmail.demo", "--run", "2", "--replay"], cwd=JOBMAIL)

    step(5, "Scout again, now reading jobmail's memory",
         "roles you already applied to are skipped, however well they score")
    run(SCAN + ["--db", os.path.join(OUT, "bridge.db"), "--reset", "--jobmail-db", JOBMAIL_DB,
                "--out", os.path.join(OUT, "digest-bridge.html")],
        keep=["Scoring", "skipped", "strong"])

    step(6, "The funnel: did strong matches turn into interviews?",
         "Scout's tiers joined to how far each application got (synthetic, so the shape, not the numbers)")
    run([sys.executable, "run.py", "funnel", "--db", os.path.join(OUT, "jobs.db"), "--jobmail-db", JOBMAIL_DB])

    step(7, "The weekly brief, evidence only (add an API key for recommendations)",
         "evidence and assumptions are written by code; held messages get their own section")
    brief = os.path.join(OUT, "brief.md")
    run([sys.executable, "-m", "jobmail.brief", "--db", JOBMAIL_DB, "--no-llm", "--out", brief], cwd=JOBMAIL)
    print("\n   A brief with model recommendations, cited by evidence id: jobmail/examples/pipeline-brief.md")
    print("   Evals: eval/scout/REPORT.md and jobmail/eval/REPORT.md")


if __name__ == "__main__":
    main()
