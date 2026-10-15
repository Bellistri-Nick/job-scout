import unittest

from jobagent.model import parse_salary, sane_comp
from tests.fixtures import job


class ParseSalaryTest(unittest.TestCase):
    def test_k_notation(self):
        self.assertEqual(parse_salary("Pay: $150K - $200K"), (150000, 200000, "$150K - $200K"))

    def test_full_numbers_with_en_dash(self):
        self.assertEqual(parse_salary("$130,000 – $150,000")[:2], (130000, 150000))

    def test_word_to_separator(self):
        self.assertEqual(parse_salary("$180k to $220k")[:2], (180000, 220000))

    def test_hourly_rate_is_ignored(self):
        self.assertEqual(parse_salary("$10 - $20 per hour"), (0, 0, ""))

    def test_empty_text(self):
        self.assertEqual(parse_salary(""), (0, 0, ""))
        self.assertEqual(parse_salary(None), (0, 0, ""))


class SaneCompTest(unittest.TestCase):
    def test_plausible_range_is_kept(self):
        self.assertEqual(sane_comp(150000, 200000), (150000, 200000, "$150K - $200K"))

    def test_aggregator_junk_range_is_dropped(self):
        self.assertEqual(sane_comp(10000, 750000), (0, 0, ""))

    def test_too_wide_range_is_dropped(self):
        self.assertEqual(sane_comp(50000, 200000), (0, 0, ""))

    def test_inverted_or_missing_is_dropped(self):
        self.assertEqual(sane_comp(200000, 150000), (0, 0, ""))
        self.assertEqual(sane_comp(0, 150000), (0, 0, ""))


class FingerprintTest(unittest.TestCase):
    def test_retitled_posting_matches_original(self):
        a = job(company="Globex", title="Product Manager")
        b = job(company=" GLOBEX ", title="Senior Product Manager (Remote)")
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_different_company_does_not_match(self):
        self.assertNotEqual(job(company="Globex").fingerprint, job(company="Initech").fingerprint)

    def test_different_function_does_not_match(self):
        self.assertNotEqual(job(title="Product Manager").fingerprint,
                            job(title="Product Marketing Manager").fingerprint)


class AgeDaysTest(unittest.TestCase):
    def test_missing_or_bad_date_reads_as_unknown(self):
        self.assertEqual(job(posted="").age_days, 999)
        self.assertEqual(job(posted="last Tuesday").age_days, 999)

    def test_zulu_timestamp(self):
        self.assertGreater(job(posted="2020-01-01T00:00:00Z").age_days, 365)


if __name__ == "__main__":
    unittest.main()
