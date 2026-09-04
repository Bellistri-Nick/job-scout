"""The normalized Job record every source produces."""
import hashlib, re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(html):
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<(br|/p|/li|/div|/h[1-6])[^>]*>", "\n", text)
    text = TAG_RE.sub(" ", text)
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"'),
                 ("&lt;", "<"), ("&gt;", ">"), ("&rsquo;", "'"), ("&ndash;", "-")):
        text = text.replace(a, b)
    return WS_RE.sub(" ", text).strip()


def norm_title(t):
    t = (t or "").lower()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(remote|hybrid|us|usa|full time|fulltime|f t)\b", " ", t)
    # Strip seniority so a retitled posting does not read as a brand new role.
    t = re.sub(r"\b(sr|senior|staff|lead|principal|associate)\b", " ", t)
    return WS_RE.sub(" ", t).strip()


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    comp_text: str = ""
    comp_min: int = 0
    comp_max: int = 0
    posted: str = ""            # ISO date string, best effort
    employment_type: str = ""
    remote: bool = False
    score: int = 0
    tier: str = ""              # strong | look | long | drop
    reject: str = ""            # hard = wrong job entirely, blank = ranked normally
    reasons: list = field(default_factory=list)
    why: str = ""               # one-line LLM rationale for the email
    llm_score: int = 0

    @property
    def fingerprint(self):
        key = f"{self.company.strip().lower()}|{norm_title(self.title)}|{(self.location or '').lower()[:24]}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    @property
    def age_days(self):
        if not self.posted:
            return 999
        try:
            d = datetime.fromisoformat(self.posted.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - d).days
        except Exception:
            return 999

    def to_dict(self):
        return asdict(self)


def sane_comp(lo, hi):
    """Aggregator boards publish junk ranges ($10K-$750K). Trust only plausible ones."""
    lo, hi = int(lo or 0), int(hi or 0)
    if not (lo and hi):
        return 0, 0, ""
    if lo < 40000 or hi > 600000 or hi < lo or hi > lo * 3.5:
        return 0, 0, ""
    return lo, hi, f"${lo//1000}K - ${hi//1000}K"


SALARY_RE = re.compile(
    r"\$\s?(\d{2,3})(?:,(\d{3}))?\s?(k\b)?\s?(?:-|\u2013|to)\s?\$?\s?(\d{2,3})(?:,(\d{3}))?\s?(k\b)?",
    re.I)


def parse_salary(text):
    """Pull a (min, max, display) base-salary range out of free text. Annual USD only."""
    if not text:
        return 0, 0, ""
    for m in SALARY_RE.finditer(text[:6000]):
        def val(whole, thousands, kflag):
            n = int(whole + (thousands or ""))
            if kflag or n < 1000:
                n *= 1000
            return n
        lo = val(m.group(1), m.group(2), m.group(3))
        hi = val(m.group(4), m.group(5), m.group(6))
        if 40000 <= lo <= 600000 and lo <= hi <= 900000:
            return lo, hi, f"${lo//1000}K - ${hi//1000}K"
    return 0, 0, ""
