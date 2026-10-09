"""The v1 -> v2 bridge: Scout's sent history joined to jobmail's applications."""
import os
import sqlite3
import tempfile
import unittest

from jobagent import funnel
from jobagent.store import Store
from tests.fixtures import job


def make_jobmail(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT, role TEXT, stage TEXT);
      CREATE TABLE events (id INTEGER PRIMARY KEY, application_id INTEGER, kind TEXT, detail TEXT,
                           to_stage TEXT, at TEXT);
      INSERT INTO applications VALUES (1, 'Northbeam Analytics', 'Senior PM, AI Platform', 'rejected');
      INSERT INTO events VALUES (1, 1, 'stage_change', '', 'interviewing', '2026-10-01');
      INSERT INTO events VALUES (2, 1, 'stage_change', '', 'rejected', '2026-10-05');
      INSERT INTO applications VALUES (2, 'Halcyon Health', 'Group Product Manager, AI Care Navigation', 'screening');
    """)
    db.commit()
    db.close()


class FunnelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.jm = os.path.join(self.dir, "jobmail.db")
        make_jobmail(self.jm)

    def test_same_role_ignores_seniority_and_abbreviations(self):
        self.assertTrue(funnel.same_role("Senior Product Manager, AI Platform", "Sr. PM - AI Platform"))
        self.assertFalse(funnel.same_role("Staff Product Manager, Clinical Data",
                                          "Group Product Manager, AI Care Navigation"))

    def test_furthest_stage_survives_a_rejection(self):
        apps = {a["company"]: a for a in funnel.applications(self.jm)}
        self.assertEqual(apps["Northbeam Analytics"]["furthest"], "interviewing")

    def test_funnel_joins_by_company_and_role(self):
        store = Store(os.path.join(self.dir, "scout.db"))
        a = job(company="Northbeam Analytics", title="Senior Product Manager, AI Platform",
                url="https://example.com/1"); a.tier = "strong"
        b = job(company="Halcyon Health", title="Staff Product Manager, Clinical Data",
                url="https://example.com/2"); b.tier = "look"
        store.record(a, sent=True)
        store.record(b, sent=True)
        rows, outside = funnel.funnel(store.db, self.jm)
        self.assertEqual((rows["strong"]["applied"], rows["strong"]["interview"]), (1, 1))
        self.assertEqual(rows["look"]["applied"], 0)
        self.assertEqual([o["company"] for o in outside], ["Halcyon Health"])

    def test_jobmail_is_opened_read_only(self):
        db = funnel.open_jobmail(self.jm)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM applications")


if __name__ == "__main__":
    unittest.main()
