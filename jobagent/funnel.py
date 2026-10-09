"""The bridge from v1 to v2: what Scout emailed, joined to what jobmail saw happen.

Scout knows which roles it sent and at what tier. jobmail knows which ones you
applied to and how far each got. Neither alone can answer the question that
justifies either: do strong matches turn into interviews more often than the
rest? This module reads jobmail's SQLite file (read-only, never writes) and
joins the two on company plus role.

Stdlib only, like the rest of Scout. The matching mirrors jobmail's own
normalisation closely enough to join, without importing it.
"""
import re
import sqlite3

STAGE_ORDER = ["applied", "screening", "interviewing", "assessment", "offer"]

_SUFFIXES = r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|sa|group|holdings|technologies|technology|labs)\b"
_ABBREV = {"pm": "product manager", "tpm": "technical product manager", "gpm": "group product manager",
           "sr": "senior", "mgr": "manager"}
_STOP = {"senior", "sr", "lead", "principal", "staff", "ii", "iii", "of", "the", "and", "product", "manager"}


def norm_company(name):
    s = re.sub(r"[.,'\"()]+", " ", (name or "").lower())
    s = re.sub(_SUFFIXES, " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _role_tokens(role):
    words = re.sub(r"[^a-z0-9]+", " ", (role or "").lower()).split()
    return {t for w in words for t in _ABBREV.get(w, w).split()} - _STOP


def same_role(a, b):
    """Two titles name the same role. Seniority words and 'product manager' are
    ignored, so this keys on the area: 'AI Platform', 'Clinical Data'."""
    ta, tb = _role_tokens(a), _role_tokens(b)
    if not ta or not tb:
        return True  # an unknown or generic role never blocks a company match, as in jobmail
    return len(ta & tb) / len(ta | tb) >= 0.5


def open_jobmail(path):
    """Read-only connection; a missing or locked file means no bridge, not a failed scan."""
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def applications(path):
    """Every jobmail application with the furthest stage it ever reached."""
    db = open_jobmail(path)
    if db is None:
        return []
    db.row_factory = sqlite3.Row
    apps = []
    for a in db.execute("SELECT id, company, role, stage FROM applications"):
        reached = {r[0] for r in db.execute(
            "SELECT to_stage FROM events WHERE application_id=? AND to_stage IS NOT NULL", (a["id"],))}
        reached.add(a["stage"])
        best = max((STAGE_ORDER.index(s) for s in reached if s in STAGE_ORDER), default=0)
        apps.append({"id": a["id"], "company": a["company"], "role": a["role"] or "",
                     "stage": a["stage"], "furthest": STAGE_ORDER[best]})
    db.close()
    return apps


def find_application(company, title, apps):
    key = norm_company(company)
    for a in apps:
        if norm_company(a["company"]) == key and same_role(a["role"], title):
            return a
    return None


def funnel(scout_db, jobmail_path):
    """Per tier: emailed, applied, advanced (screening or beyond), reached an interview."""
    apps = applications(jobmail_path)
    sent = scout_db.execute(
        "SELECT company, title, tier, score FROM jobs WHERE sent_at IS NOT NULL").fetchall()
    rows, matched = {}, set()
    for company, title, tier, _ in sent:
        r = rows.setdefault(tier, {"emailed": 0, "applied": 0, "advanced": 0, "interview": 0, "roles": []})
        r["emailed"] += 1
        a = find_application(company, title, apps)
        if not a:
            continue
        matched.add(a["id"])
        r["applied"] += 1
        i = STAGE_ORDER.index(a["furthest"])
        r["advanced"] += i >= 1
        r["interview"] += i >= 2
        r["roles"].append(f"{company}: {title} -> {a['stage']} (furthest: {a['furthest']})")
    outside = [a for a in apps if a["id"] not in matched]
    return rows, outside


def render(rows, outside):
    order = ["strong", "look", "long"]
    out = ["Scout to jobmail funnel", "",
           f"{'tier':8} {'emailed':>8} {'applied':>8} {'advanced':>9} {'interview':>10}  apply rate  interview rate",
           "-" * 80]
    for tier in order + sorted(set(rows) - set(order)):
        if tier not in rows:
            continue
        r = rows[tier]
        ar = f"{r['applied'] / r['emailed']:.0%}" if r["emailed"] else "-"
        ir = f"{r['interview'] / r['applied']:.0%}" if r["applied"] else "-"
        out.append(f"{tier:8} {r['emailed']:>8} {r['applied']:>8} {r['advanced']:>9} {r['interview']:>10}  "
                   f"{ar:>10}  {ir:>14}")
    for tier in order:
        for line in rows.get(tier, {}).get("roles", []):
            out.append(f"  [{tier}] {line}")
    out += ["", f"Applications Scout never emailed ({len(outside)}): "
                "recruiter outreach, referrals, or roles found elsewhere"]
    out += [f"  {a['company']}: {a['role'] or 'role unknown'} -> {a['stage']}" for a in outside] or ["  none"]
    return "\n".join(out)
