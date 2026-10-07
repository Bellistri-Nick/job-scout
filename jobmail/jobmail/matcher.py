"""Link a classified message to an application record.

Order of preference:
1. Threading headers (In-Reply-To / References) pointing at a stored message.
2. Normalised company name match, preferring an open application whose role
   matches (or when only one application exists for that company).
3. Sender domain previously seen on an application (skipping shared ATS /
   job-board domains, which would otherwise glue unrelated companies together).
4. Otherwise: create a new application if the message is job related and has
   a company, else leave unlinked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .classifier import Classification
from .db import OPEN_STAGES, Database
from .mail import ParsedMessage

log = logging.getLogger(__name__)

# Domains that many employers send from; never use these for matching.
SHARED_DOMAINS = {
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co",
    "myworkday.com", "myworkdayjobs.com", "workday.com", "ashbyhq.com",
    "icims.com", "smartrecruiters.com", "jobvite.com", "breezy.hr",
    "bamboohr.com", "recruitee.com", "workable.com", "applytojob.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "wellfound.com", "hired.com", "calendly.com", "goodtime.io",
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
}

# Where an application arrived through. The sender domain of the first message
# is the honest answer: a Greenhouse auto-ack means you applied via Greenhouse.
ATS_LABELS = {
    "greenhouse.io": "Greenhouse", "greenhouse-mail.io": "Greenhouse",
    "lever.co": "Lever", "hire.lever.co": "Lever",
    "myworkday.com": "Workday", "myworkdayjobs.com": "Workday", "workday.com": "Workday",
    "ashbyhq.com": "Ashby", "icims.com": "iCIMS", "smartrecruiters.com": "SmartRecruiters",
    "jobvite.com": "Jobvite", "breezy.hr": "Breezy", "bamboohr.com": "BambooHR",
    "recruitee.com": "Recruitee", "workable.com": "Workable", "applytojob.com": "JazzHR",
    "linkedin.com": "LinkedIn", "indeed.com": "Indeed", "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter", "wellfound.com": "Wellfound", "hired.com": "Hired",
}


def source_for(domain: str | None) -> str | None:
    """Label the channel an application came in through, if recognisable."""
    if not domain:
        return None
    d = domain.lower()
    for known, label in ATS_LABELS.items():
        if d == known or d.endswith("." + known):
            return label
    return "Direct" if d not in SHARED_DOMAINS else None


_LEGAL_SUFFIXES = r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|sa|group|holdings|technologies|technology|labs)\b"


def normalise_company(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[.,'\"()]+", " ", s)
    s = re.sub(_LEGAL_SUFFIXES, " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def normalise_role(role: str) -> str:
    s = role.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def roles_similar(a: str, b: str) -> bool:
    a, b = normalise_role(a), normalise_role(b)
    if not a or not b:
        return True  # unknown role: don't block a match
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    stop = {"senior", "sr", "lead", "principal", "staff", "ii", "iii", "of", "the", "and"}
    ta, tb = ta - stop, tb - stop
    if not ta or not tb:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.5


@dataclass
class MatchResult:
    application_id: int | None
    created: bool
    how: str


class Matcher:
    def __init__(self, db: Database):
        self.db = db

    def match(self, msg: ParsedMessage, cls: Classification) -> MatchResult:
        # 1. threading
        for ref in [msg.in_reply_to, *reversed(msg.references)]:
            if not ref:
                continue
            prior = self.db.find_message_by_message_id(ref)
            if prior and prior["application_id"]:
                return MatchResult(int(prior["application_id"]), False, "thread")

        if not cls.is_job_related or cls.message_type in {"newsletter_or_job_alert", "not_job_related"}:
            return MatchResult(None, False, "not_job_related")

        # 2. company name
        key = normalise_company(cls.company)
        if key:
            candidates = self.db.find_applications_by_company(key)
            if candidates:
                open_c = [c for c in candidates if c["stage"] in OPEN_STAGES]
                pool = open_c or candidates
                if len(pool) == 1:
                    return MatchResult(int(pool[0]["id"]), False, "company")
                for c in pool:
                    if roles_similar(c["role"] or "", cls.role):
                        return MatchResult(int(c["id"]), False, "company+role")
                return MatchResult(int(pool[0]["id"]), False, "company(first)")

        # 3. sender domain
        domain = msg.from_domain
        if domain and domain not in SHARED_DOMAINS:
            by_domain = self.db.find_applications_by_domain(domain)
            open_d = [c for c in by_domain if c["stage"] in OPEN_STAGES]
            pool = open_d or by_domain
            if pool:
                for c in pool:
                    if roles_similar(c["role"] or "", cls.role):
                        return MatchResult(int(c["id"]), False, "domain")
                return MatchResult(int(pool[0]["id"]), False, "domain(first)")

        # 4. create
        if key:
            stage = "applied"
            app_id = self.db.create_application(
                company=cls.company,
                company_key=key,
                role=cls.role or None,
                stage=stage,
                source=source_for(domain),
                sender_domains=[domain] if domain and domain not in SHARED_DOMAINS else [],
                applied_at=msg.sent_at,
            )
            return MatchResult(app_id, True, "created")

        return MatchResult(None, False, "no_company")
