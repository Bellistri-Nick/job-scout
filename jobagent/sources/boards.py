"""Aggregator boards with open JSON feeds: Remotive, RemoteOK, Himalayas.

These cover remote roles at companies that will never be on a watchlist. They
are keyword-queried, so they widen the net without widening the noise much.
"""
import time
from datetime import datetime, timezone

from ..http import get_json
from ..model import Job, sane_comp, strip_html
from ..terms import is_in_field, search_queries

REMOTIVE = "https://remotive.com/api/remote-jobs?search={q}&limit=60"
REMOTEOK = "https://remoteok.com/api"
HIMALAYAS = "https://himalayas.app/jobs/api?limit=100&offset={off}"




def _epoch(v):
    try:
        return datetime.fromtimestamp(int(v), timezone.utc).date().isoformat()
    except Exception:
        return ""


def remotive(profile, queries=None):
    seen, out = set(), []
    for q in (queries or search_queries(profile)):
        data = get_json(REMOTIVE.format(q=q.replace(" ", "%20")))
        for j in (data or {}).get("jobs", []):
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            out.append(Job(
                source="remotive",
                company=j.get("company_name", ""),
                title=j.get("title", ""),
                url=j.get("url", ""),
                location=j.get("candidate_required_location", "") or "Remote",
                description=strip_html(j.get("description", ""))[:6000],
                comp_text=j.get("salary", "") or "",
                employment_type=j.get("job_type", ""),
                remote=True,
                posted=(j.get("publication_date") or "")[:10],
            ))
    return out


def remoteok(profile):
    data = get_json(REMOTEOK)
    if not isinstance(data, list):
        return []
    out = []
    for j in data[1:]:  # element 0 is the legal/ToS notice
        title = (j.get("position") or j.get("title") or "")
        tags = " ".join(j.get("tags") or []).lower()
        if not is_in_field(title + " " + tags, profile):
            continue
        lo, hi, disp = sane_comp(j.get("salary_min"), j.get("salary_max"))
        out.append(Job(
            source="remoteok",
            company=j.get("company", ""),
            title=title,
            url=j.get("url") or j.get("apply_url", ""),
            location=j.get("location") or "Remote",
            description=strip_html(j.get("description", ""))[:6000],
            comp_min=lo, comp_max=hi, comp_text=disp,
            remote=True,
            posted=(j.get("date") or "")[:10],
        ))
    return out


def _expired(job):
    try:
        return int(job.get("expiryDate") or 0) < int(time.time())
    except Exception:
        return False


def himalayas(profile, pages=3):
    out = []
    for page in range(pages):
        data = get_json(HIMALAYAS.format(off=page * 100), timeout=45)
        jobs = (data or {}).get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            if _expired(j):
                continue
            title = j.get("title", "")
            cats = " ".join(j.get("categories") or []).lower()
            if not is_in_field(title + " " + cats, profile):
                continue
            lo, hi, disp = sane_comp(j.get("minSalary"), j.get("maxSalary"))
            locs = ", ".join(j.get("locationRestrictions") or []) or "Remote"
            out.append(Job(
                source="himalayas",
                company=j.get("companyName", ""),
                title=title,
                url=j.get("applicationLink") or j.get("guid", ""),
                location=locs,
                description=strip_html(j.get("description", ""))[:6000],
                comp_min=lo, comp_max=hi, comp_text=disp,
                employment_type=j.get("employmentType", ""),
                remote=True,
                posted=_epoch(j.get("pubDate")),
            ))
    return out
