"""Search vocabulary derived from the profile.

Every source needs to know two things: which words mean "this is my field" (for cheap
pre-filtering) and which phrases to type into a keyword search. Both come from the
profile's target titles, so the engine carries no assumptions about what job you want.

A profile can override either one with "search_terms" and "function_words".
"""
import re
from collections import Counter

# Words that describe level or shape, not function. They say nothing about the field.
_NOISE = {
    "senior", "sr", "junior", "jr", "staff", "lead", "principal", "head", "chief",
    "director", "manager", "vp", "vice", "president", "associate", "assistant",
    "of", "and", "the", "for", "a", "an", "to", "in", "at", "with", "ii", "iii", "iv",
    "i", "level", "remote", "hybrid", "us", "usa", "full", "time", "part",
    "managing", "supervisor", "team", "global", "regional", "group", "sr", "jr",
}


def stem(word):
    """Crude suffix trim so one title form covers its relatives: copywriter and
    copywriting both reduce to copywrit, editor and editorial to edit."""
    for suffix in ("ing", "ers", "ors", "ist", "er", "or", "al", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def function_words(profile, limit=6):
    """The words that actually mark a posting as in-field.

    Splitting every target title into words is too generous: "product copywriter"
    contributes "product", which then matches every product role on every board.
    So rank stems by how many target titles use them and keep only the top handful.
    A word the profile leans on repeatedly is the field; a word it mentions once is
    incidental.
    """
    override = profile.get("function_words")
    if override:
        return {w.lower() for w in override}

    counts = Counter()
    for key in ("titles_tier1", "titles_tier2"):
        for title in profile.get(key) or []:
            for word in set(re.split(r"[^a-z0-9]+", title.lower())):
                if word and word not in _NOISE and len(word) > 2:
                    counts[stem(word)] += 1

    # Collapse fragments: "editori" is subsumed by "edit", so merge it down. Without
    # this, a frequency cut can keep "editori" and drop "edit", which would stop
    # matching the word "editor" entirely.
    for longer in sorted(counts, key=len, reverse=True):
        for shorter in counts:
            if shorter != longer and longer.startswith(shorter):
                counts[shorter] += counts.pop(longer)
                break

    return {w for w, _ in counts.most_common(limit)}


def is_in_field(title, profile, _cache={}):
    """Permissive on purpose. Scoring makes the real decision; this just avoids
    paying for detail fetches on obviously unrelated postings."""
    key = id(profile)
    if key not in _cache:
        _cache[key] = function_words(profile)
    words = _cache[key]
    if not words:
        return True
    text = title.lower()
    # Prefix match, not whole-word: "copy" must still catch "copywriting", and
    # "design" must catch "designer". This is a net, not a decision.
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w), text) for w in words)


def search_queries(profile, limit=8):
    """Phrases to hand a keyword-search API. Target titles are already the best queries."""
    override = profile.get("search_terms")
    if override:
        return override[:limit]
    seen, out = set(), []
    for key in ("titles_tier1", "titles_tier2"):
        for title in profile.get(key) or []:
            t = title.strip().lower()
            if t and t not in seen and 3 < len(t) < 40:
                seen.add(t)
                out.append(t)
            if len(out) >= limit:
                return out
    return out
