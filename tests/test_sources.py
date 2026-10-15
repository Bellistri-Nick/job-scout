"""Workable, SmartRecruiters and LinkedIn: parsing and the guard rails. All offline."""
import os
import unittest
from unittest import mock

from jobagent import discover
from jobagent.sources import ats, linkedin
from tests.fixtures import profile

# Trimmed from a real logged-out search response (October 2026).
CARDS = """
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:4477929343">
    <h3 class="base-search-card__title">
      AI Builder, Principal Product Manager
    </h3>
    <h4 class="base-search-card__subtitle">
      <a class="hidden-nested-link" href="https://www.linkedin.com/company/autodesk">
        Autodesk
      </a>
    </h4>
    <span class="job-search-card__location">
      Boston, MA
    </span>
    <time class="job-search-card__listdate" datetime="2026-10-08">5 days ago</time>
  </div>
</li>
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:111">
    <h3 class="base-search-card__title">Senior Product Manager &amp; Growth</h3>
    <h4 class="base-search-card__subtitle">Northbeam</h4>
    <span class="job-search-card__location">Remote</span>
  </div>
</li>
<li><div>an ad slot with no posting urn</div></li>
"""

DETAIL = """<div class="description__text">
<div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5
            relative overflow-hidden"><p>Own the <strong>agent</strong> roadmap.</p><ul><li>Evals</li></ul></div>
</div>"""


class LinkedInParsingTest(unittest.TestCase):
    def test_cards_are_parsed_and_unescaped(self):
        cards = linkedin.parse_cards(CARDS)
        self.assertEqual([c["id"] for c in cards], ["4477929343", "111"])
        self.assertEqual(cards[0]["title"], "AI Builder, Principal Product Manager")
        self.assertEqual(cards[0]["company"], "Autodesk")
        self.assertEqual(cards[0]["location"], "Boston, MA")
        self.assertEqual(cards[0]["posted"], "2026-10-08")
        self.assertEqual(cards[1]["title"], "Senior Product Manager & Growth")
        self.assertEqual(cards[1]["posted"], "")

    def test_description_comes_out_as_plain_text(self):
        self.assertEqual(linkedin.parse_description(DETAIL), "Own the agent roadmap. Evals")

    def test_missing_description_block_is_empty_not_an_error(self):
        self.assertEqual(linkedin.parse_description("<html>login wall</html>"), "")


class LinkedInGuardTest(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCOUT_LINKEDIN", None)
            self.assertFalse(linkedin.enabled())
            self.assertTrue(linkedin.enabled(True))

    def test_env_var_turns_it_on(self):
        with mock.patch.dict(os.environ, {"SCOUT_LINKEDIN": "1"}):
            self.assertTrue(linkedin.enabled())

    def test_a_block_stops_the_run_and_returns_what_it_had(self):
        calls = []

        def fake_get(url, **kw):
            calls.append(url)
            return CARDS if len(calls) == 1 else None  # first page fine, then blocked

        with mock.patch.object(linkedin, "get", fake_get), \
                mock.patch.object(linkedin, "PAUSE", 0):
            jobs = linkedin.search(profile(), queries=["product manager"], pages=3,
                                   where="Boston, MA")
        # one good search page, one blocked page, then no further requests of any kind
        self.assertEqual(len([u for u in calls if "seeMoreJobPostings" in u]), 2)
        self.assertEqual({j.source for j in jobs}, {"linkedin"})
        self.assertTrue(all(j.url.startswith("https://www.linkedin.com/jobs/view/") for j in jobs))

    def test_out_of_field_titles_cost_no_detail_fetch(self):
        cards = CARDS.replace("AI Builder, Principal Product Manager", "Barista")
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            if "seeMoreJobPostings" in url:
                return cards if "start=0" in url and "f_WT" not in url else ""
            return DETAIL

        with mock.patch.object(linkedin, "get", fake_get), mock.patch.object(linkedin, "PAUSE", 0):
            jobs = linkedin.search(profile(), queries=["product manager"], pages=1,
                                   where="Boston, MA")
        details = [u for u in seen if "/jobPosting/" in u]
        self.assertEqual(len(details), 1)       # only the PM card
        self.assertEqual([j.title for j in jobs], ["Senior Product Manager & Growth"])


    def test_after_a_block_no_detail_pages_are_requested(self):
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            return CARDS if len(seen) == 1 else None  # one good page, then 429s

        with mock.patch.object(linkedin, "get", fake_get), mock.patch.object(linkedin, "PAUSE", 0):
            jobs = linkedin.search(profile(), queries=["product manager"], pages=3,
                                   where="Boston, MA")
        self.assertEqual([u for u in seen if "/jobPosting/" in u], [])
        self.assertTrue(jobs)                      # cards already collected still come through
        self.assertTrue(all(j.description == "" for j in jobs))

    def test_a_block_on_a_detail_page_stops_further_detail_fetches(self):
        many = CARDS + CARDS.replace("4477929343", "222").replace("urn:li:jobPosting:111", "urn:li:jobPosting:333")
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            return many if "seeMoreJobPostings" in url and "start=0" in url and "f_WT" not in url                 else ("" if "seeMoreJobPostings" in url else None)

        with mock.patch.object(linkedin, "get", fake_get), mock.patch.object(linkedin, "PAUSE", 0):
            linkedin.search(profile(), queries=["product manager"], pages=1, where="Boston, MA")
        self.assertEqual(len([u for u in seen if "/jobPosting/" in u]), 1)


WORKABLE = {"name": "Acme", "jobs": [
    {"title": "Senior Product Manager, AI", "shortcode": "A1", "employment_type": "Full-time",
     "telecommuting": True, "url": "https://apply.workable.com/j/A1", "published_on": "2026-10-01",
     "city": "Boston", "state": "Massachusetts", "country": "United States",
     "description": "<p>Own the <b>evals</b> roadmap.</p>"},
    {"title": "Barista", "url": "https://apply.workable.com/j/B2", "description": "<p>coffee</p>"},
]}


class WorkableTest(unittest.TestCase):
    def test_maps_in_field_postings_and_drops_the_rest(self):
        with mock.patch.object(ats, "get_json", return_value=WORKABLE):
            jobs = ats.workable("acme", "Acme", profile())
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual((j.source, j.company, j.remote), ("workable", "Acme", True))
        self.assertEqual(j.location, "Boston, Massachusetts, United States")
        self.assertEqual(j.description, "Own the evals roadmap.")
        self.assertEqual(j.posted, "2026-10-01")

    def test_unknown_account_is_an_empty_list(self):
        with mock.patch.object(ats, "get_json", return_value=None):
            self.assertEqual(ats.workable("nope", "Nope", profile()), [])


def _sr_list(*names):
    return {"content": [{"id": str(i), "name": n, "releasedDate": "2026-10-12T05:00:00.000Z",
                         "typeOfEmployment": {"label": "Full-time"},
                         "location": {"fullLocation": "Boston, Massachusetts, United States",
                                      "remote": False}}
                        for i, n in enumerate(names)]}


SR_DETAIL = {"postingUrl": "https://jobs.smartrecruiters.com/Acme/1-pm",
             "jobAd": {"sections": {"jobDescription": {"text": "<p>Lead <i>agent</i> quality.</p>"},
                                    "qualifications": {"text": "<ul><li>SQL</li></ul>"}}}}


class SmartRecruitersTest(unittest.TestCase):
    def test_details_are_fetched_only_for_in_field_postings(self):
        urls = []

        def fake(url, **kw):
            urls.append(url)
            return _sr_list("Barista", "Senior Product Manager", "Sales Engineer") \
                if "?limit=100" in url else SR_DETAIL

        with mock.patch.object(ats, "get_json", fake):
            jobs = ats.smartrecruiters("Orbit", "Orbit Labs", profile())
        self.assertEqual([j.title for j in jobs], ["Senior Product Manager"])
        self.assertEqual(len([u for u in urls if "/postings/" in u]), 1)
        self.assertEqual(jobs[0].url, "https://jobs.smartrecruiters.com/Acme/1-pm")
        self.assertEqual(jobs[0].description, "Lead agent quality. SQL")
        self.assertEqual(jobs[0].posted, "2026-10-12")

    def test_pages_until_a_short_page(self):
        full = {"content": [{"id": str(i), "name": "Barista"} for i in range(100)]}
        pages = [full, full, {"content": []}]
        with mock.patch.object(ats, "get_json", side_effect=pages) as m:
            ats.smartrecruiters("Orbit", "Orbit Labs", profile(), max_pages=8)
        self.assertEqual(m.call_count, 3)


class WatchlistAndDiscoveryTest(unittest.TestCase):
    def test_watchlist_dispatches_to_the_new_fetchers(self):
        self.assertIn("workable", ats.FETCHERS)
        self.assertIn("smartrecruiters", ats.FETCHERS)

    def test_smartrecruiters_empty_board_is_not_a_hit(self):
        # SmartRecruiters answers any slug with 200 and an empty list.
        empty = {"offset": 0, "limit": 1, "totalFound": 0, "content": []}
        with mock.patch.object(discover, "get_json", return_value=empty):
            self.assertIsNone(discover.probe("Nonexistent Co", verbose=False))

    def test_smartrecruiters_count_uses_total_found(self):
        self.assertEqual(discover._open_roles({"totalFound": 696, "content": [{}]}), 696)
        self.assertEqual(discover._open_roles({"jobs": [1, 2]}), 2)
        self.assertEqual(discover._open_roles([1, 2, 3]), 3)

    def test_probe_finds_a_workable_board(self):
        def fake(url, **kw):
            if "workable.com" in url:
                return {"name": "Acme", "jobs": [{"title": "PM"}]}
            return None

        with mock.patch.object(discover, "get_json", fake):
            hit = discover.probe("Acme", verbose=False)
        self.assertEqual((hit["ats"], hit["slug"], hit["open_roles"]), ("workable", "acme", 1))


if __name__ == "__main__":
    unittest.main()
