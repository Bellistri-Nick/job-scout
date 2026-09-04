"""Rule-based scoring. Cheap, deterministic, and runs before any LLM call."""
import re

from .model import parse_salary

_CACHE = {}


def _pat(needle):
    """Word-boundary match so 'intern' does not fire on 'internal communications'
    and 'gis' does not fire on 'strategist'."""
    if needle not in _CACHE:
        parts = [re.escape(w) for w in needle.split()]
        body = r"\s+".join(parts)
        _CACHE[needle] = re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.I)
    return _CACHE[needle]


def _has(text, needles):
    t = text or ""
    return [n for n in needles if _pat(n).search(t)]


def score_job(job, p):
    """Score 0-100 against the profile. Sets job.score, job.tier, job.reasons."""
    title = (job.title or "").lower()
    loc = (job.location or "").lower()
    desc = (job.description or "").lower()
    blob = f"{title} {loc} {desc}"
    reasons, score = [], 0

    # --- hard rejects -------------------------------------------------
    hit = _has(title, p["title_block"])
    if hit:
        job.tier, job.reject, job.reasons = "drop", "hard", [f"blocked title: {hit[0]}"]
        return job
    hit = _has(title, p["junior_block"])
    if hit:
        job.tier, job.reject, job.reasons = "drop", "hard", [f"too junior: {hit[0]}"]
        return job
    company = job.company.strip().lower()
    if company in [c.lower() for c in p.get("companies_skip", [])]:
        job.tier, job.reject, job.reasons = "drop", "hard", ["company on skip list"]
        return job
    hit = _has(company, p.get("company_skip_patterns", []))
    if hit:
        job.tier, job.reject, job.reasons = "drop", "hard", [f"staffing firm ({hit[0]})"]
        return job

    # --- title fit ----------------------------------------------------
    t1 = _has(title, p["titles_tier1"])
    t2 = _has(title, p["titles_tier2"])
    if t1:
        score += 40
        reasons.append(f"target title ({t1[0]})")
    elif t2:
        score += 25
        reasons.append(f"adjacent title ({t2[0]})")
    else:
        job.tier, job.reject, job.reasons = "drop", "hard", ["title not in your target function"]
        return job

    # --- seniority ----------------------------------------------------
    if _has(title, p["seniority_lead"]):
        score += 15
        reasons.append("leadership scope in title")
    elif _has(title, p["seniority_senior"]):
        score += 9
        reasons.append("senior level")

    # --- location -----------------------------------------------------
    blocked_loc = _has(loc, p["locations_block"])
    allowed_loc = _has(loc, p["locations_allow"])
    if blocked_loc and not allowed_loc:
        score -= 60
        reasons.append(f"outside her range ({job.location})")
    elif "remote" in loc or job.remote:
        if p.get("us_only_remote") and blocked_loc:
            score -= 45
            reasons.append("remote but not US-eligible")
        else:
            score += 20
            reasons.append("remote")
    elif any(k in loc for k in (", ma", "boston", "massachusetts", "cambridge", "quincy",
                                "foxboro", "foxborough", "needham", "waltham", "newton", "hingham")):
        score += 20
        reasons.append("Boston metro / commutable")
    elif allowed_loc:
        score += 8
        reasons.append("US-based")
    else:
        score -= 35
        reasons.append(f"location questionable ({job.location or 'unspecified'})")

    # --- compensation -------------------------------------------------
    lo, hi, disp = job.comp_min, job.comp_max, job.comp_text
    if not lo:
        lo, hi, disp = parse_salary(job.description)
        if lo:
            job.comp_min, job.comp_max, job.comp_text = lo, hi, disp
    floor, target = p["comp_floor"], p["comp_target"]
    if hi:
        if hi >= target:
            score += 12
            reasons.append(f"comp {disp}")
        elif hi >= floor:
            score += 6
            reasons.append(f"comp {disp} (at floor)")
        else:
            score -= 25
            reasons.append(f"comp below floor ({disp})")

    # --- content of the posting ---------------------------------------
    strong = _has(desc, p["keywords_strong"])
    good = _has(desc, p["keywords_good"])
    score += min(len(strong) * 4, 16)
    score += min(len(good) * 2, 10)
    if strong:
        reasons.append("matches: " + ", ".join(strong[:4]))
    neg = _has(title + " " + desc, p["keywords_negative"])
    if neg:
        score -= 12 * min(len(neg), 2)
        reasons.append(f"flag: {neg[0]}")

    # --- freshness ----------------------------------------------------
    max_age = p.get("max_age_days", 45)
    if job.age_days != 999 and job.age_days > max_age:
        score -= 18
        reasons.append(f"posted {job.age_days}d ago")
    if job.age_days <= 7:
        score += 6
        reasons.append(f"posted {job.age_days}d ago")
    elif job.age_days <= 14:
        score += 3

    job.score = max(0, min(100, score))
    th = p["thresholds"]
    job.tier = ("strong" if job.score >= th["strong"]
                else "look" if job.score >= th["look"] else "long")
    job.reasons = reasons
    return job
