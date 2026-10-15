"""The scan eval: asymmetric errors are counted separately."""
import unittest

from jobagent.evaluate import summarise

POSTINGS = [
    {"id": "a", "title": "Staff PM", "company": "X", "url": "u1", "expect": ["strong"]},
    {"id": "b", "title": "Staff PM", "company": "Y", "url": "u2", "expect": ["strong", "look"]},
    {"id": "c", "title": "Senior PM", "company": "Z", "url": "u3", "expect": ["long", "look"]},
    {"id": "d", "title": "APM", "company": "Z", "url": "u4", "expect": ["drop"]},
    {"id": "e", "title": "Senior PM", "company": "W", "url": "u5", "expect": ["look"]},
]


class SummariseTest(unittest.TestCase):
    def test_strong_or_look_role_filed_as_look_is_not_a_missed_strong(self):
        s = summarise(POSTINGS, {"a": "strong", "b": "look", "c": "long", "d": "drop", "e": "look"})
        self.assertEqual(s["strong_recall"], 1.0)
        self.assertEqual(s["tier_accuracy"], 1.0)

    def test_a_good_role_hard_dropped_is_reported_as_a_false_drop(self):
        s = summarise(POSTINGS, {"a": "strong", "b": "strong", "c": "long", "d": "drop", "e": "drop"})
        self.assertEqual(s["false_drops"], ["e"])

    def test_noise_in_strong_lowers_precision(self):
        s = summarise(POSTINGS, {"a": "strong", "b": "strong", "c": "strong", "d": "drop", "e": "look"})
        self.assertAlmostEqual(s["strong_precision"], 2 / 3, places=3)


if __name__ == "__main__":
    unittest.main()
