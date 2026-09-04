"""Optional Claude pass: re-rank the shortlist and write the "why this fits" line.

Rules do the filtering. The model does the judgment rules can't: whether a posting
actually wants what it says, or just borrowed the vocabulary. Only jobs that
clear the rule floor are sent here, so the cost stays small.
"""
import json
import os

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "fit": {"type": "integer", "description": "0-100 fit for this candidate"},
                    "why": {"type": "string", "description": "One sentence, 22 words max, concrete"},
                    "flag": {"type": "string", "description": "Biggest concern, or empty string"},
                },
                "required": ["id", "fit", "why", "flag"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

SYSTEM = """You screen job postings for one specific candidate. You are strict and concrete.

CANDIDATE
{summary}

WHAT A GREAT FIT LOOKS LIKE
{fit_signals}
- Base salary at or above {floor} where stated
- Location: {location_rule}

WHAT IS NOT A FIT
{anti_signals}

Score fit 0-100. Be honest: most postings land between 40 and 70. Reserve 85 and above
for roles that match the candidate's actual scope. The "why" line gets read in an email
first thing in the morning, so lead with the specific reason. No preamble, no hype,
no em-dashes."""


def build_system(profile):
    """The prompt is assembled from the profile, so this file stays candidate-agnostic."""
    def bullets(key, fallback):
        items = profile.get(key) or []
        return "\n".join(f"- {i}" for i in items) if items else fallback

    home = profile.get("home_base", "")
    remote_rule = "Remote and US-eligible" if profile.get("us_only_remote", True) else "Remote"
    location_rule = f"{remote_rule}, or commutable from {home}" if home else remote_rule
    return SYSTEM.format(
        summary=profile.get("resume_summary", "(no summary provided)"),
        fit_signals=bullets("fit_signals", "- Matches the candidate's target titles and seniority"),
        anti_signals=bullets("anti_signals", "- Junior individual contributor or coordinator level"),
        floor="${:,}".format(profile.get("comp_floor", 0)),
        location_rule=location_rule,
    )


def rerank(jobs, profile, model=None, verbose=True):
    """Mutate jobs in place, setting llm_score and why. Returns True if the pass ran."""
    if not jobs:
        return False
    try:
        import anthropic
    except ImportError:
        if verbose:
            print("    (anthropic SDK not installed, skipping LLM pass)")
        return False
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        if verbose:
            print("    (no ANTHROPIC_API_KEY, skipping LLM pass)")
        return False

    model = model or os.getenv("JOBAGENT_MODEL", "claude-opus-5")
    payload = [{
        "id": i,
        "title": j.title,
        "company": j.company,
        "location": j.location,
        "comp": j.comp_text or "not stated",
        "posted": j.posted,
        "description": (j.description or "")[:2500],
    } for i, j in enumerate(jobs)]

    system = build_system(profile)
    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content":
                       "Score every posting below. Return exactly one verdict per id.\n\n"
                       + json.dumps(payload, ensure_ascii=False)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except Exception as e:
        print("    ! LLM pass failed ({}: {}), using rule scores".format(type(e).__name__, e))
        return False

    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        verdicts = json.loads(text)["verdicts"]
    except Exception:
        print("    ! could not parse LLM response, using rule scores")
        return False

    for v in verdicts:
        idx = v.get("id")
        if not isinstance(idx, int) or not 0 <= idx < len(jobs):
            continue
        job = jobs[idx]
        job.llm_score = int(v.get("fit", 0))
        job.why = (v.get("why") or "").strip()
        if v.get("flag"):
            job.reasons.append("flag: " + v["flag"])
        # Rules keep the floor honest, the model moves it.
        job.score = int(round(0.4 * job.score + 0.6 * job.llm_score))

    th = profile["thresholds"]
    for job in jobs:
        job.tier = ("strong" if job.score >= th["strong"]
                    else "look" if job.score >= th["look"] else "long")
    if verbose:
        u = resp.usage
        print("    LLM re-rank: {} scored ({} in / {} out tokens)".format(
            len(verdicts), u.input_tokens, u.output_tokens))
    return True
