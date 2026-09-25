import unittest

from jobagent.score import hard_reject_reason, score_job
from tests.fixtures import days_ago, job, profile


class HardRejectTest(unittest.TestCase):
    def setUp(self):
        self.p = profile()

    def test_blocked_title(self):
        self.assertEqual(hard_reject_reason("Software Engineer", "Globex", self.p),
                         "blocked title: engineer")

    def test_junior_title(self):
        self.assertEqual(hard_reject_reason("Product Manager Intern", "Globex", self.p),
                         "too junior: intern")

    def test_word_boundary_intern_does_not_match_internal(self):
        self.assertIsNone(hard_reject_reason("Product Manager, Internal Tools", "Globex", self.p))

    def test_skip_list_is_case_insensitive(self):
        self.assertEqual(hard_reject_reason("Product Manager", "  ACME ", self.p),
                         "company on skip list")

    def test_staffing_firm_pattern(self):
        self.assertEqual(hard_reject_reason("Product Manager", "Bright Staffing Group", self.p),
                         "staffing firm (staffing)")

    def test_off_function_title(self):
        self.assertEqual(hard_reject_reason("Marketing Director", "Globex", self.p),
                         "title not in your target function")

    def test_adjacent_title_passes(self):
        self.assertIsNone(hard_reject_reason("Product Owner", "Globex", self.p))


class ScoreJobTest(unittest.TestCase):
    def setUp(self):
        self.p = profile()

    def test_hard_reject_drops_job(self):
        j = score_job(job(title="Sales Lead"), self.p)
        self.assertEqual((j.tier, j.reject), ("drop", "hard"))
        self.assertEqual(j.reasons, ["blocked title: sales"])

    def test_full_weighting_for_a_strong_match(self):
        # 40 title + 9 senior + 20 remote + 12 comp at target
        # + 8 two strong keywords + 2 one good keyword + 6 fresh = 97
        j = score_job(job(title="Senior Product Manager", comp_min=190000, comp_max=210000,
                          comp_text="$190K - $210K", posted=days_ago(1),
                          description="Own evals and agentic workflows. Python a plus."),
                      self.p)
        self.assertEqual(j.score, 97)
        self.assertEqual(j.tier, "strong")

    def test_tier1_outscores_tier2(self):
        t1 = score_job(job(title="Product Manager"), self.p)
        t2 = score_job(job(title="Product Owner"), self.p)
        self.assertEqual(t1.score - t2.score, 15)

    def test_blocked_location_is_penalized_not_rejected(self):
        j = score_job(job(location="Bangalore, India"), self.p)
        self.assertNotEqual(j.reject, "hard")
        self.assertIn("outside her range (Bangalore, India)", j.reasons)

    def test_remote_outside_us_is_penalized(self):
        j = score_job(job(location="Remote - Germany"), self.p)
        self.assertIn("remote but not US-eligible", j.reasons)
        self.assertNotIn("remote", j.reasons)

    def test_boston_metro_counts_as_local(self):
        j = score_job(job(location="Boston, MA"), self.p)
        self.assertIn("Boston metro / commutable", j.reasons)

    def test_comp_parsed_from_description_and_below_floor(self):
        j = score_job(job(description="Base pay: $130,000 – $150,000 USD"), self.p)
        self.assertEqual((j.comp_min, j.comp_max), (130000, 150000))
        self.assertIn("comp below floor ($130K - $150K)", j.reasons)

    def test_comp_between_floor_and_target(self):
        j = score_job(job(comp_min=170000, comp_max=190000, comp_text="$170K - $190K"), self.p)
        self.assertIn("comp $170K - $190K (at floor)", j.reasons)

    def test_strong_keywords_are_capped(self):
        four = score_job(job(description="evals context engineering agentic rag"), self.p)
        five = score_job(job(description="evals context engineering agentic rag golden set"), self.p)
        self.assertEqual(four.score, five.score)

    def test_negative_keyword_flags(self):
        clean = score_job(job(description="evals"), self.p)
        flagged = score_job(job(description="evals with a quarterly quota"), self.p)
        self.assertEqual(clean.score - flagged.score, 12)
        self.assertIn("flag: quota", flagged.reasons)

    def test_stale_posting_is_penalized(self):
        fresh = score_job(job(posted=days_ago(20)), self.p)
        stale = score_job(job(posted=days_ago(60)), self.p)
        self.assertEqual(fresh.score - stale.score, 18)

    def test_undated_posting_is_not_treated_as_stale(self):
        undated = score_job(job(posted=""), self.p)
        dated = score_job(job(posted=days_ago(20)), self.p)
        self.assertEqual(undated.score, dated.score)

    def test_score_is_clamped_at_zero(self):
        j = score_job(job(title="Product Owner", location="Remote - India", posted=days_ago(90),
                          description="$50,000 - $60,000, quota, quota"), self.p)
        self.assertEqual(j.score, 0)
        self.assertEqual(j.tier, "long")


if __name__ == "__main__":
    unittest.main()
