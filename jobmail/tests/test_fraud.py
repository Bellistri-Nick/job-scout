"""Recruiting fraud: what the model flags, what memory flags, and what a held message may not do."""
from __future__ import annotations

import json
import sqlite3

from jobmail.db import Database
from jobmail.matcher import lookalike_of
from jobmail.obsidian import ObsidianWriter
from jobmail.pipeline import Pipeline
from jobmail.triage import SkillSpec

from conftest import CapturingAlerter, StubClassifier, make_email

KNOWN = ["northbeam.example", "quarrylabs.example"]


def test_lookalike_domains_are_caught():
    assert lookalike_of("northbeam-careers.example", KNOWN) == "northbeam.example"
    assert lookalike_of("n0rthbeam.example", KNOWN) == "northbeam.example"
    assert lookalike_of("northbeam.co", KNOWN) == "northbeam.example"
    assert lookalike_of("quarry-labs.example", KNOWN) == "quarrylabs.example"


def test_real_domains_are_not_lookalikes():
    assert lookalike_of("northbeam.example", KNOWN) is None             # the domain itself
    assert lookalike_of("careers.northbeam.example", KNOWN) is None     # its own subdomain
    assert lookalike_of("ferncliff.example", KNOWN) is None             # unrelated company
    assert lookalike_of("greenhouse-mail.io", KNOWN) is None            # shared ATS domain
    assert lookalike_of("gmail.com", KNOWN) is None                     # consumer mail is the model's call
    assert lookalike_of("acme.io", ["acme.com"]) is None                # names under 5 chars collide too often


def _pipeline(cfg, db, table):
    alerter = CapturingAlerter()
    return Pipeline(cfg, db, StubClassifier(table), alerter,
                    ObsidianWriter(cfg.obsidian_vault_path, "Applications")), alerter


NORTHBEAM = {"company": "Northbeam Analytics", "role": "Senior Product Manager, AI Platform"}


def _seed_northbeam(p, days_ago):
    p.process_inbound(make_email(1, frm="Marcus Lee <marcus.lee@northbeam.example>",
                                 subject="Northbeam: next step", body="Can you send times?",
                                 msg_id="<n1@northbeam.example>", when=days_ago(10)))


def test_lookalike_sender_is_held_and_never_learned(cfg, db, days_ago):
    table = {"next step": {**NORTHBEAM, "message_type": "interview_request", "needs_reply": True,
                           "urgency": "high", "action_needed": "Send times"},
             "updated link": {**NORTHBEAM, "message_type": "scheduling", "needs_reply": True,
                              "urgency": "high", "action_needed": "Confirm on the new portal"}}
    p, alerter = _pipeline(cfg, db, table)
    _seed_northbeam(p, days_ago)
    app = db.list_applications()[0]
    assert app["stage"] == "interviewing"

    row_id = p.process_inbound(make_email(
        2, frm="Marcus Lee <marcus.lee@northbeam-careers.example>",
        subject="Northbeam: updated link for your final round", body="Please confirm on our new portal.",
        msg_id="<n2@northbeam-careers.example>", when=days_ago(1)))
    row = db.get_message(row_id)

    assert row["suspected_fraud"] == 2                   # poses as Northbeam: alert
    assert "lookalike_sender_domain" in json.loads(row["fraud_signals"])
    assert row["application_id"] is None                 # not linked to the real application
    assert row["needs_reply"] == 0                       # never "reply to the scammer"
    assert "northbeam.example" in row["action_needed"]   # tells you which domain is the real one
    assert p.last_match.how == "held(fraud)"
    assert "northbeam-careers.example" not in db.trusted_domains()   # memory not poisoned
    assert db.get_application(app["id"])["stage"] == "interviewing"
    assert not [r for r in db.list_needs_reply() if r["id"] == row_id]

    p.send_alerts()
    held = [s for s, _ in alerter.sent if "Possible scam" in s]
    assert held == ["[jobmail] Possible scam, verify before acting: claims to be Northbeam Analytics"]
    p.send_alerts()
    assert len([s for s, _ in alerter.sent if "Possible scam" in s]) == 1   # once


def test_same_email_from_the_real_domain_links_normally(cfg, db, days_ago):
    table = {"next step": {**NORTHBEAM, "message_type": "interview_request", "needs_reply": True},
             "updated link": {**NORTHBEAM, "message_type": "scheduling", "needs_reply": True}}
    p, _ = _pipeline(cfg, db, table)
    _seed_northbeam(p, days_ago)
    row_id = p.process_inbound(make_email(
        2, frm="Marcus Lee <marcus.lee@careers.northbeam.example>",
        subject="Northbeam: updated link for your final round", body="Please confirm.",
        msg_id="<n3@careers.northbeam.example>", when=days_ago(1)))
    row = db.get_message(row_id)
    assert row["suspected_fraud"] == 0
    assert row["application_id"] == db.list_applications()[0]["id"]


def test_model_flagged_scam_creates_no_application(cfg, db, days_ago):
    table = {"selected": {"company": "Global Remote Staffing", "role": "Product Data Assistant",
                          "message_type": "offer", "suspected_fraud": True,
                          "fraud_signals": ["offer_without_interview", "asks_for_money_or_bank_details"]}}
    p, alerter = _pipeline(cfg, db, table)
    row_id = p.process_inbound(make_email(
        1, frm="HR <hr.grs@gmail.com>", subject="You have been selected", body="Send your bank details.",
        msg_id="<s1@gmail.com>", when=days_ago(1)))
    assert db.list_applications() == []        # an "offer" from a scammer is not an application
    assert db.get_message(row_id)["suspected_fraud"] == 1
    p.send_alerts()
    assert alerter.sent == []                  # generic scam: held quietly, nobody paged


def test_scam_naming_a_company_you_applied_to_alerts(cfg, db, days_ago):
    table = {"next step": {**NORTHBEAM, "message_type": "interview_request", "needs_reply": True},
             "payroll": {**NORTHBEAM, "message_type": "offer", "suspected_fraud": True,
                         "fraud_signals": ["asks_for_money_or_bank_details"]}}
    p, alerter = _pipeline(cfg, db, table)
    _seed_northbeam(p, days_ago)
    row_id = p.process_inbound(make_email(
        2, frm="Northbeam HR <northbeam.hr@gmail.com>", subject="Northbeam payroll setup",
        body="Send your bank details.", msg_id="<s2@gmail.com>", when=days_ago(1)))
    assert db.get_message(row_id)["suspected_fraud"] == 2
    p.send_alerts()
    assert any("Possible scam" in s and "Northbeam" in s for s, _ in alerter.sent)


def test_invariant_overrides_a_model_that_says_reply():
    spec = SkillSpec.load("job_inbox")
    rec = {"suspected_fraud": True, "needs_reply": True, "urgency": "low", "action_needed": "Send your ID"}
    fired = spec.enforce(rec)
    assert fired and rec["needs_reply"] is False and rec["urgency"] == "high"
    assert "Do not reply" in rec["action_needed"]


def test_fallback_is_not_marked_as_fraud():
    # A failed call pages a human as "read this yourself", not as a scam.
    assert SkillSpec.load("job_inbox").fallback["suspected_fraud"] is False


def test_migration_adds_fraud_columns(tmp_path):
    path = tmp_path / "old.db"
    Database(path).close()
    con = sqlite3.connect(path)   # make it look like a database from before this change
    con.execute("ALTER TABLE messages DROP COLUMN suspected_fraud")
    con.execute("ALTER TABLE messages DROP COLUMN fraud_signals")
    con.commit()
    con.close()
    db = Database(path)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(messages)")}
    db.close()
    assert {"suspected_fraud", "fraud_signals"} <= cols
