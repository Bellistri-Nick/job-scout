"""The brief: evidence comes from the database, and recommendations must cite it."""

from __future__ import annotations

import json

from jobmail.brief import build_brief, evidence_ids, evidence_pack, ground
from jobmail.pipeline import Pipeline

from conftest import CapturingAlerter, StubClassifier, make_email


def _seed(cfg, db, days_ago):
    p = Pipeline(cfg, db, StubClassifier({
        "applied": {"company": "Acme", "role": "PM", "message_type": "application_confirmation"},
        "times": {"company": "Acme", "role": "PM", "message_type": "interview_request", "needs_reply": True,
                  "urgency": "high", "action_needed": "Send times"},
        "cold": {"company": "Globex", "role": "GPM", "message_type": "recruiter_outreach", "needs_reply": True,
                 "urgency": "medium", "action_needed": "Reply if interested"},
        "selected": {"company": "", "message_type": "other_job_related", "summary": "Looks like a scam"},
    }), CapturingAlerter())
    p.process_inbound(make_email(1, frm="x@greenhouse.io", subject="applied", body=".", msg_id="<1@a>", when=days_ago(30)))
    p.process_inbound(make_email(2, frm="r@acme.example", subject="times?", body=".", msg_id="<2@a>", when=days_ago(1)))
    p.process_inbound(make_email(3, frm="g@globex.example", subject="cold hello", body=".", msg_id="<3@a>", when=days_ago(3)))
    p.process_inbound(make_email(4, frm="hr@gmail.com", subject="You were selected", body=".", msg_id="<4@a>", when=days_ago(2)))


def test_evidence_pack_has_ids_for_everything(cfg, db, days_ago):
    _seed(cfg, db, days_ago)
    pack = evidence_pack(db)
    assert {a["company"] for a in pack["open_asks"]} == {"Acme", "Globex"}
    assert [n["reason"] for n in pack["needs_review"]] == ["job-related but not linked to any application"]
    ids = evidence_ids(pack)
    assert all(a["id"] in ids for a in pack["open_asks"]) and "S1" in ids


def test_ground_drops_invented_citations_and_flags_uncovered_asks(cfg, db, days_ago):
    _seed(cfg, db, days_ago)
    pack = evidence_pack(db)
    acme = next(a for a in pack["open_asks"] if a["company"] == "Acme")
    globex = next(a for a in pack["open_asks"] if a["company"] == "Globex")
    answer = {"recommendations": [
        {"priority": "this_week", "action": "Reply to Globex", "why": ".", "evidence": [globex["id"], "M999"]},
        {"priority": "consider", "action": "Apply to Initech", "why": ".", "evidence": ["A42"]},
    ], "pipeline_observation": "", "assumptions": []}
    kept, notes = ground(answer, pack)
    assert [r["action"] for r in kept] == ["Reply to Globex"] and kept[0]["evidence"] == [globex["id"]]
    assert any("M999" in n for n in notes)
    assert any("Initech" in n and "no supporting evidence" in n for n in notes)
    assert any(acme["id"] in n and "not covered" in n for n in notes)   # the high-urgency ask nobody cited


def test_brief_without_model_still_renders_evidence(cfg, db, days_ago):
    _seed(cfg, db, days_ago)
    text, _ = build_brief(db, use_llm=False)
    for section in ("## Recommendations", "## Evidence", "### Open asks (2)", "## Assumptions", "System assumptions"):
        assert section in text


def test_brief_survives_a_failed_model_call(cfg, db, days_ago):
    _seed(cfg, db, days_ago)

    class Down:
        messages = None
        def __init__(self): self.messages = self
        def create(self, **kw): raise RuntimeError("503")

    text, _ = build_brief(db, client=Down())
    assert "model call failed" in text and "### Open asks (2)" in text
