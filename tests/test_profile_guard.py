"""The profile guards: what code fixes after a draft, and what it warns about."""
import copy
import json
import os
import unittest

import run
from jobagent import profile as guard


def _load(*parts):
    with open(os.path.join(run.HERE, *parts), encoding="utf-8") as fh:
        return json.load(fh)


class ProfileGuardTest(unittest.TestCase):
    def setUp(self):
        self.template = _load("config", "profile.example.json")
        self.good = _load("samples", "scout", "profile.json")

    def test_reviewed_sample_profile_is_clean(self):
        summary, warnings = guard.check(self.good, self.template)
        self.assertEqual(warnings, [])
        self.assertTrue(any(line.startswith("Pay: base floor $180,000") for line in summary))

    def test_a_draft_cannot_change_how_much_email_arrives(self):
        """The real draft in samples/ loosened the thresholds from 80/58 to 75/55."""
        draft = _load("samples", "scout", "profile.draft.json")
        self.assertNotEqual(draft["thresholds"], self.template["thresholds"])
        notes = guard.finalize(draft, self.template)
        self.assertEqual(draft["thresholds"], self.template["thresholds"])
        self.assertTrue(any("thresholds reset" in n for n in notes))

    def test_blocking_your_own_target_title_is_flagged(self):
        p = copy.deepcopy(self.good)
        p["title_block"] = p["title_block"] + ["platform"]
        _, warnings = guard.check(p, self.template)
        self.assertTrue(any('"platform" is blocked' in w for w in warnings), warnings)

    def test_silent_failures_are_named(self):
        p = copy.deepcopy(self.good)
        p.update(comp_floor=0, home_base="", locations_local=[], titles_tier1=p["titles_tier1"][:2],
                 resume_summary="PM.", fit_signals=[])
        _, warnings = guard.check(p, self.template)
        for phrase in ("no comp floor", "no home base", "only 2 target titles", "resume_summary is thin",
                       "no fit_signals"):
            self.assertTrue(any(phrase in w for w in warnings), phrase)

    def test_missing_fields_stop_the_check(self):
        p = copy.deepcopy(self.good)
        del p["comp_floor"]
        summary, warnings = guard.check(p, self.template)
        self.assertEqual(summary, [])
        self.assertIn("missing fields: comp_floor", warnings)


if __name__ == "__main__":
    unittest.main()
