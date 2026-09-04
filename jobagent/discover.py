"""Resolve which ATS a company uses by probing the three public board APIs.

Run this when you add companies to the watchlist. It writes back only verified
hits, so the daily scan never wastes fetches on guesses.
"""
import json
import re

from .http import get_json

PROBES = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=false",
     lambda d: isinstance(d, dict) and isinstance(d.get("jobs"), list)),
    ("lever", "https://api.lever.co/v0/postings/{s}?mode=json",
     lambda d: isinstance(d, list)),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{s}",
     lambda d: isinstance(d, dict) and isinstance(d.get("jobs"), list)),
]


def slug_variants(name):
    base = name.strip().lower()
    plain = re.sub(r"[^a-z0-9]+", "", base)
    dashed = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    nosuffix = re.sub(r"\b(inc|llc|corp|co|company|group|brands|the)\b", "", base)
    nosuffix = re.sub(r"[^a-z0-9]+", "", nosuffix)
    out = []
    for s in (plain, dashed, nosuffix):
        if s and s not in out:
            out.append(s)
    return out


def probe(name, slugs=None, verbose=True):
    """Return the first verified {ats, slug, jobs} for a company, or None."""
    for slug in (slugs or slug_variants(name)):
        for ats, url, ok in PROBES:
            data = get_json(url.format(s=slug), timeout=25, retries=1)
            if data is not None and ok(data):
                count = len(data) if isinstance(data, list) else len(data.get("jobs", []))
                if count == 0:
                    continue  # empty board, usually a wrong-but-valid slug
                if verbose:
                    print(f"  {name}: {ats}/{slug} ({count} open roles)")
                return {"name": name, "ats": ats, "slug": slug, "open_roles": count}
    if verbose:
        print(f"  {name}: no public board found (custom or Workday)")
    return None


def resolve(names, existing_path=None, verbose=True):
    """Probe a list of company names, merging into an existing watchlist file."""
    existing = []
    if existing_path:
        try:
            with open(existing_path, encoding="utf-8") as fh:
                existing = json.load(fh).get("companies", [])
        except Exception:
            existing = []
    known = {c["name"].lower() for c in existing}
    for name in names:
        if name.lower() in known:
            continue
        hit = probe(name, verbose=verbose)
        if hit:
            existing.append(hit)
    return existing
