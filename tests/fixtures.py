from datetime import datetime, timedelta, timezone

from jobagent.model import Job


def profile():
    return {
        "titles_tier1": ["product manager"],
        "titles_tier2": ["product owner"],
        "title_block": ["engineer", "sales"],
        "junior_block": ["intern", "junior"],
        "seniority_lead": ["director", "head of", "principal", "lead"],
        "seniority_senior": ["senior", "staff"],
        "locations_allow": ["remote", "united states"],
        "locations_block": ["india", "germany"],
        "us_only_remote": True,
        "comp_floor": 180000,
        "comp_target": 200000,
        "keywords_strong": ["evals", "context engineering", "agentic", "rag", "golden set"],
        "keywords_good": ["sql", "python"],
        "keywords_negative": ["quota"],
        "companies_skip": ["Acme"],
        "company_skip_patterns": ["staffing", "recruit"],
        "max_age_days": 45,
        "thresholds": {"strong": 80, "look": 58, "llm_floor": 45},
    }


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def job(**kw):
    base = dict(source="test", company="Globex", title="Product Manager",
                url="https://example.com/jobs/1", location="Remote",
                posted=days_ago(20))
    base.update(kw)
    return Job(**base)
