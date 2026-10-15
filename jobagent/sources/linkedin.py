"""LinkedIn public job search - opt-in, off by default.

LinkedIn serves a logged-out search at /jobs-guest/jobs/api/seeMoreJobPostings/search
and a logged-out detail page per posting. No login, no key, a real recency filter. It is
not an API: this is scraping public HTML. LinkedIn's terms prohibit automated access, and it
can change the markup or rate-limit an address at any time. So this source is off until you turn it
on (`run.py scan --linkedin`, or SCOUT_LINKEDIN=1), runs slowly on purpose, and gives up
for the day at the first block instead of pushing through it.

Volume is capped per run. Detail pages are fetched only for postings whose title is in
your field and that the rules would not reject outright.
"""
import html
import os
import re
import time
from urllib.parse import quote

from ..http import get
from ..model import Job, strip_html
from ..score import hard_reject_reason
from ..terms import is_in_field, search_queries

SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
          "?keywords={q}&location={loc}&f_TPR=r{secs}&start={start}{remote}")
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
VIEW = "https://www.linkedin.com/jobs/view/{jid}"

# LinkedIn serves a stripped page to non-browser agents.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_SIZE = 25
PAUSE = 5.0           # seconds between requests; 2s drew a 429 after about nine
MAX_DETAILS = 40      # detail fetches per run


def enabled(flag=False):
    return bool(flag) or os.getenv("SCOUT_LINKEDIN", "").strip().lower() in ("1", "true", "yes")


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def parse_cards(markup):
    """Search-result HTML -> [{id, title, company, location, posted}]."""
    cards = []
    for block in re.split(r"<li>", markup or ""):
        m = re.search(r"urn:li:jobPosting:(\d+)", block)
        t = re.search(r'base-search-card__title">(.*?)</h3>', block, re.S)
        if not (m and t):
            continue
        c = re.search(r'base-search-card__subtitle">(.*?)</h4>', block, re.S)
        loc = re.search(r'job-search-card__location">(.*?)</span>', block, re.S)
        d = re.search(r'<time[^>]*datetime="([\d-]+)"', block)
        cards.append({
            "id": m.group(1),
            "title": _clean(strip_html(t.group(1))),
            "company": _clean(strip_html(c.group(1))) if c else "",
            "location": _clean(strip_html(loc.group(1))) if loc else "",
            "posted": d.group(1) if d else "",
        })
    return cards


def parse_description(markup):
    """Detail-page HTML -> plain-text description ('' if the block is missing)."""
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', markup or "", re.S)
    return _clean(strip_html(m.group(1)))[:6000] if m else ""


def search(profile, queries=None, days=7, pages=2, where=None):
    """Return Jobs. Stops for the run if LinkedIn blocks or rate-limits a request."""
    queries = queries or search_queries(profile, limit=4)
    home = where if where is not None else profile.get("home_base", "")
    # (location text, remote-only filter): the home area, then remote anywhere in the US
    targets = ([(home, "")] if home else []) + [("United States", "&f_WT=2")]

    cards, seen = [], set()
    for q in queries:
        for loc, remote in targets:
            for p in range(pages):
                body = get(SEARCH.format(q=quote(q), loc=quote(loc), secs=days * 86400,
                                         start=p * PAGE_SIZE, remote=remote),
                           headers=HEADERS, timeout=30, retries=0)
                time.sleep(PAUSE)
                if body is None:
                    print("    ! linkedin: blocked or rate-limited, stopping for this run")
                    # No detail fetches either: they would hit the same wall. The cards
                    # already collected still go through, without descriptions.
                    return _to_jobs(cards, profile, fetch_details=False)
                batch = parse_cards(body)
                for card in batch:
                    if card["id"] not in seen:
                        seen.add(card["id"])
                        card["remote"] = bool(remote)
                        cards.append(card)
                if len(batch) < PAGE_SIZE:
                    break
    return _to_jobs(cards, profile)


def _to_jobs(cards, profile, fetch_details=True):
    jobs, fetched = [], 0
    for card in cards:
        if not is_in_field(card["title"], profile):
            continue
        if hard_reject_reason(card["title"], card["company"], profile):
            continue
        desc = ""
        if fetch_details and fetched < MAX_DETAILS:
            fetched += 1
            body = get(DETAIL.format(jid=card["id"]), headers=HEADERS, timeout=30, retries=0)
            time.sleep(PAUSE)
            if body is None:
                print("    ! linkedin: blocked on a detail page, no more detail fetches")
                fetch_details = False
            else:
                desc = parse_description(body)
        jobs.append(Job(
            source="linkedin",
            company=card["company"],
            title=card["title"],
            url=VIEW.format(jid=card["id"]),
            location=card["location"],
            description=desc,
            remote=card.get("remote", False),
            posted=card["posted"],
        ))
    return jobs
