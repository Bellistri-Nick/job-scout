"""Adzuna aggregate search - optional, free developer key.

Adzuna indexes the wide middle of the market (agency, retail, publishing, local
Boston employers) that never shows up on startup ATS boards. Register at
https://developer.adzuna.com and put ADZUNA_APP_ID / ADZUNA_APP_KEY in .env.
Without keys this source silently returns nothing.
"""
import os
from ..http import get_json
from ..model import Job, sane_comp
from ..terms import search_queries

URL = ("https://api.adzuna.com/v1/api/jobs/us/search/{page}"
       "?app_id={aid}&app_key={akey}&results_per_page=50&what={what}"
       "&where={where}&distance={dist}&max_days_old={days}&content-type=application/json")




def search(profile, queries=None, where=None, distance=45, days=21, remote_too=True):
    aid, akey = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (aid and akey):
        return []
    out = []
    for what in (queries or search_queries(profile, limit=5)):
        home = where if where is not None else profile.get("home_base", "")
        targets = [(home, distance)] if home else []
        targets += [("", 0)] if remote_too else []
        for loc, dist in targets:
            data = get_json(URL.format(page=1, aid=aid, akey=akey, days=days, dist=dist or 200,
                                       what=what.replace(" ", "%20"), where=loc.replace(" ", "%20")))
            for j in (data or {}).get("results", []):
                lo, hi, disp = sane_comp(j.get("salary_min"), j.get("salary_max"))
                out.append(Job(
                    source="adzuna",
                    company=(j.get("company") or {}).get("display_name", ""),
                    title=j.get("title", ""),
                    url=j.get("redirect_url", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    description=(j.get("description") or "")[:6000],
                    comp_min=lo, comp_max=hi, comp_text=disp,
                    employment_type=j.get("contract_time", ""),
                    posted=(j.get("created") or "")[:10],
                ))
    return out
