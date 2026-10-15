"""Guards for a drafted profile: what code fixes, and what a human should look at.

The profile decides every score, so a wrong field fails silently: a comp floor
of 0 lets underpaid roles through, and a block phrase that matches your own
target title hides every role you want. These checks run after a draft and on
demand (`run.py profile-check`), so the setup interview ends with a readback
of facts, not a JSON file nobody reads.
"""
from .score import _has


def finalize(profile, template):
    """Fix in code what a draft must not decide. Returns notes on what changed.

    Thresholds control how much email arrives. The first draft in testing
    loosened them from 80/58 to 75/55 despite instructions, so a draft never
    sets them: they start at the template and the user tunes them later.
    """
    notes = []
    if profile.get("thresholds") != template["thresholds"]:
        if profile.get("thresholds"):
            notes.append("thresholds reset to the defaults {}; tune them after a dry run".format(
                template["thresholds"]))
        profile["thresholds"] = dict(template["thresholds"])
    for key in template:
        if not key.startswith("_") and key not in profile:
            profile[key] = template[key]
            notes.append(f"{key} missing from the draft; took the template default")
    return notes


def check(profile, template):
    """(summary lines, warnings). Warnings are things that silently change results."""
    p, w = profile, []
    missing = [k for k in template if not k.startswith("_") and k not in p]
    if missing:
        w.append("missing fields: " + ", ".join(missing))
        return [], w

    tier1, tier2 = p.get("titles_tier1", []), p.get("titles_tier2", [])
    floor, target = p.get("comp_floor", 0), p.get("comp_target", 0)

    if len(tier1) < 5:
        w.append(f"only {len(tier1)} target titles; real postings use many variants, so roles will be missed")
    self_blocked = [(t, b) for t in tier1 + tier2 for b in _has(t, p.get("title_block", []) + p.get("junior_block", []))]
    for t, b in self_blocked[:5]:
        w.append(f'"{b}" is blocked, but it matches your own target title "{t}": those roles are dropped')
    if not floor:
        w.append("no comp floor: low-paying roles are never penalized")
    elif target and target < floor:
        w.append(f"comp target ${target:,} is below the floor ${floor:,}")
    if not p.get("home_base") and not p.get("locations_local"):
        w.append("no home base or commute towns: only remote roles earn location points")
    if len(p.get("resume_summary", "")) < 200:
        w.append("resume_summary is thin; the model re-rank judges fit from it")
    if not p.get("fit_signals"):
        w.append("no fit_signals: the model has no stated idea of a strong fit")
    if p.get("thresholds") != template["thresholds"]:
        w.append("thresholds differ from the defaults {}: expect {} email".format(
            template["thresholds"],
            "more" if p["thresholds"].get("look", 0) < template["thresholds"]["look"] else "less"))

    remote = "remote, US only" if p.get("us_only_remote", True) else "remote, anywhere"
    commute = "; ".join(p.get("locations_local", [])[:6])
    summary = [
        f"Searching for: {p.get('headline') or '(no headline)'}",
        f"Target titles ({len(tier1)}): {'; '.join(tier1[:8])}{' ...' if len(tier1) > 8 else ''}",
        f"Also shown, ranked lower ({len(tier2)}): {'; '.join(tier2[:5])}{' ...' if len(tier2) > 5 else ''}",
        f"Never shown: {len(p.get('title_block', []))} functions, {len(p.get('junior_block', []))} junior titles, "
        f"{len(p.get('companies_skip', []))} companies",
        f"Where: {remote}" + (f"; or commutable from {p['home_base']} ({commute}{' ...' if len(p.get('locations_local', [])) > 6 else ''})"
                              if p.get("home_base") else ""),
        f"Pay: base floor ${floor:,}, target ${target:,}",
        f"Email: strong at {p['thresholds']['strong']}+, worth a look at {p['thresholds']['look']}+",
    ]
    return summary, w
