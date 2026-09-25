import os
import tempfile
import unittest

from jobagent.store import Store
from run import dedupe, hold_back_unreviewed
from tests.fixtures import job


class InRunDedupeTest(unittest.TestCase):
    def test_jobs_without_url_are_dropped(self):
        self.assertEqual(dedupe([job(url="")]), [])

    def test_same_role_in_several_cities_folds_into_one(self):
        out = dedupe([job(location="Boston, MA", url="u1"),
                      job(location="New York, NY", url="u2"),
                      job(location="Austin, TX", url="u3")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].location, "Boston, MA / New York, NY / Austin, TX")

    def test_repeated_city_is_not_appended_twice(self):
        out = dedupe([job(location="Boston, MA", url="u1"), job(location="boston, ma", url="u2")])
        self.assertEqual(out[0].location, "Boston, MA")

    def test_folded_location_is_capped(self):
        out = dedupe([job(location=f"City {i:03d}, ST", url=f"u{i}") for i in range(20)])
        self.assertLessEqual(len(out[0].location), 120)

    def test_distinct_roles_survive(self):
        out = dedupe([job(company="Globex"), job(company="Initech")])
        self.assertEqual(len(out), 2)


class HoldBackUnreviewedTest(unittest.TestCase):
    def test_only_unreviewed_strong_roles_are_demoted(self):
        reviewed = job(tier="strong", reviewed=True)
        unreviewed = job(tier="strong", reviewed=False)
        look = job(tier="look", reviewed=False)
        self.assertEqual(hold_back_unreviewed([reviewed, unreviewed, look]), 1)
        self.assertEqual([reviewed.tier, unreviewed.tier, look.tier], ["strong", "look", "look"])


class StoreDedupeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(os.path.join(tmp.name, "jobs.db"))
        self.addCleanup(self.store.db.close)

    def test_recorded_job_is_no_longer_new(self):
        j = job()
        self.assertTrue(self.store.is_new(j))
        self.store.record(j)
        self.assertFalse(self.store.is_new(j))

    def test_retitled_repost_is_not_new(self):
        self.store.record(job(title="Product Manager", url="u1"))
        self.assertFalse(self.store.is_new(job(title="Senior Product Manager", url="u2")))

    def test_same_url_is_not_new_even_with_new_title(self):
        self.store.record(job(title="Product Manager", url="https://x.com/1"))
        self.assertFalse(self.store.is_new(job(title="Product Owner", url="https://x.com/1")))

    def test_blank_urls_do_not_collide(self):
        self.store.record(job(company="Globex", url=""))
        self.assertTrue(self.store.is_new(job(company="Initech", url="")))

    def test_rescore_keeps_original_sent_time(self):
        j = job()
        self.store.record(j, sent=True)
        self.store.record(j, sent=False)
        self.assertEqual(self.store.stats()["emailed"], 1)
        self.assertEqual(self.store.stats()["tracked"], 1)


if __name__ == "__main__":
    unittest.main()
