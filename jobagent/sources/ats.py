"""Greenhouse, Lever, and Ashby public job-board APIs.

These are the documented public endpoints that power each company's own careers
page. No key, no login, no scraping. One fetch per company per run.
"""
from datetime import datetime, timezone

from ..http import get_json
from ..model import Job, strip_html
from ..score import hard_reject_reason
from ..terms import is_in_field

GREENHOUSE_LIST = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
GREENHOUSE_ONE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def _iso(value):
    if not value:
        return ""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(secs, timezone.utc).date().isoformat()
    return str(value)[:10]


def _title_interesting(title, profile):
    """Cheap pre-filter so we only pay for detail fetches on plausible roles.
    The vocabulary comes from the profile, so this works for any field."""
    return is_in_field(title, profile)


def greenhouse(slug, company, profile):
    data = get_json(GREENHOUSE_LIST.format(slug=slug))
    if not data or "jobs" not in data:
        return []
    out = []
    for j in data["jobs"]:
        title = j.get("title", "")
        if not _title_interesting(title, profile):
            continue
        # Greenhouse is the one source that costs a request per posting. Reject on the
        # title before spending it: on a 600-role board that is the difference between
        # a few dozen fetches and a few hundred.
        if hard_reject_reason(title, company, profile):
            continue
        detail = get_json(GREENHOUSE_ONE.format(slug=slug, jid=j.get("id"))) or {}
        out.append(Job(
            source="greenhouse",
            company=company,
            title=title,
            url=j.get("absolute_url", ""),
            location=(j.get("location") or {}).get("name", ""),
            description=strip_html(detail.get("content", ""))[:6000],
            posted=_iso(j.get("updated_at") or j.get("first_published")),
        ))
    return out


def lever(slug, company, profile):
    data = get_json(LEVER.format(slug=slug))
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        title = j.get("text", "")
        if not _title_interesting(title, profile):
            continue
        cats = j.get("categories") or {}
        desc = strip_html(j.get("descriptionPlain") or j.get("description", ""))
        lists = " ".join(strip_html(s.get("text", "") + " " + s.get("content", ""))
                         for s in (j.get("lists") or []))
        out.append(Job(
            source="lever",
            company=company,
            title=title,
            url=j.get("hostedUrl", ""),
            location=cats.get("location", "") or (j.get("workplaceType") or ""),
            description=(desc + " " + lists)[:6000],
            employment_type=cats.get("commitment", ""),
            remote=(j.get("workplaceType", "") or "").lower() == "remote",
            comp_text=(j.get("salaryRange") or {}).get("min") and
                      f"${int(j['salaryRange']['min'])//1000}K - ${int(j['salaryRange']['max'])//1000}K" or "",
            comp_min=int((j.get("salaryRange") or {}).get("min") or 0),
            comp_max=int((j.get("salaryRange") or {}).get("max") or 0),
            posted=_iso(j.get("createdAt")),
        ))
    return out


def _ashby_comp(j):
    comp = j.get("compensation") or {}
    summary = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or ""
    lo = hi = 0
    for tier in comp.get("compensationTiers") or []:
        for c in tier.get("components") or []:
            if c.get("compensationType") == "Salary" and c.get("interval") == "1 YEAR":
                if c.get("minValue"):
                    lo = max(lo, int(c["minValue"]))
                if c.get("maxValue"):
                    hi = max(hi, int(c["maxValue"]))
    return summary, lo, hi


def ashby(slug, company, profile):
    data = get_json(ASHBY.format(slug=slug), timeout=45)
    if not data or "jobs" not in data:
        return []
    out = []
    for j in data["jobs"]:
        title = j.get("title", "").strip()
        if not _title_interesting(title, profile):
            continue
        summary, lo, hi = _ashby_comp(j)
        out.append(Job(
            source="ashby",
            company=company,
            title=title,
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            location=j.get("location", ""),
            description=strip_html(j.get("descriptionHtml", ""))[:6000],
            employment_type=j.get("employmentType", ""),
            remote=bool(j.get("isRemote")),
            comp_text=summary, comp_min=lo, comp_max=hi,
            posted=_iso(j.get("publishedAt")),
        ))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch_watchlist(companies, profile, verbose=True):
    """companies: [{"name":..., "ats":"greenhouse|lever|ashby", "slug":...}, ...]"""
    jobs = []
    for c in companies:
        fn = FETCHERS.get(c.get("ats"))
        if not fn:
            continue
        found = fn(c["slug"], c["name"], profile)
        jobs.extend(found)
        if verbose and found:
            print(f"    {c['name']} ({c['ats']}): {len(found)} in-function")
    return jobs
