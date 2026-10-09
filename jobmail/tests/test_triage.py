"""The triage skill engine: request shape, fail-loud fallback, and code-enforced invariants."""

from __future__ import annotations

import json

import pytest

from jobmail.classifier import MESSAGE_TYPES, ClaudeClassifier
from jobmail.triage import SkillSpec, Triage

from conftest import make_email


class FakeResp:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 900, "output_tokens": 120})()


class FakeClient:
    def __init__(self, answer=None, exc: Exception | None = None):
        self.answer, self.exc, self.calls = answer, exc, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return FakeResp(self.answer if isinstance(self.answer, str) else json.dumps(self.answer))


def job_record(**over):
    rec = {"is_job_related": True, "company": "Acme", "role": "PM", "message_type": "interview_request",
           "sender_is_human": True, "needs_reply": True, "urgency": "high", "summary": "s",
           "action_needed": "Send times", "key_dates": [], "contact_name": "Jane"}
    rec.update(over)
    return rec


def ap_record(**over):
    rec = {"is_ap_related": True, "vendor": "Northwind", "message_type": "vendor_detail_change",
           "invoice_number": "", "po_number": "", "amount": "", "currency": "", "due_date": "",
           "needs_reply": True, "urgency": "medium", "risk_flags": ["bank_detail_change"],
           "requires_human_verification": False, "summary": "s", "action_needed": "Update bank details"}
    rec.update(over)
    return rec


def test_request_never_forces_a_tool():
    """Forced tool_choice is a 400 on current models. This is the regression the eval caught."""
    c = FakeClient(job_record())
    for model in ("claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5"):
        Triage(SkillSpec.load("job_inbox"), model=model, client=c).run("x")
    for kw in c.calls:
        assert "tool_choice" not in kw and "tools" not in kw
        assert kw["output_config"]["format"]["type"] == "json_schema"


def test_every_object_in_every_spec_is_closed():
    """Structured outputs requires additionalProperties: false on every object."""
    def walk(node, path):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
                assert set(node.get("required", [])) == set(node.get("properties", {})), path
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    from jobmail.triage import SPEC_DIR
    specs = sorted(SPEC_DIR.glob("*.json"))
    assert len(specs) >= 3
    for path in specs:
        walk(SkillSpec.load(path).schema, path.stem)


def test_job_inbox_enum_matches_pipeline_types():
    enum = SkillSpec.load("job_inbox").schema["properties"]["message_type"]["enum"]
    assert enum == MESSAGE_TYPES


@pytest.mark.parametrize("client", [
    FakeClient(exc=RuntimeError("400 tool_choice not supported")),
    FakeClient(answer="not json"),
    FakeClient(answer=job_record(message_type="made_up_type")),
])
def test_failure_is_loud_not_silent(client):
    """Any failure must page a human: needs_reply true, urgency high."""
    clf = ClaudeClassifier("k", "claude-sonnet-5-5", client=client)
    cls = clf.classify(make_email(1, frm="jane@acme.com", subject="Can you talk Thursday?",
                                  body="...", msg_id="<a@b>"))
    assert cls.failed is True
    assert cls.needs_reply is True and cls.urgency == "high" and cls.is_job_related is True


def test_good_answer_passes_through():
    clf = ClaudeClassifier("k", "claude-sonnet-5-5", client=FakeClient(job_record()))
    cls = clf.classify(make_email(1, frm="jane@acme.com", subject="s", body="b", msg_id="<a@b>"))
    assert not cls.failed and cls.company == "Acme" and cls.message_type == "interview_request"
    assert clf.last_result.input_tokens == 900


def test_ap_invariant_overrides_the_model():
    """The model flagged a bank change but said no verification needed. Code wins."""
    t = Triage(SkillSpec.load("ap_inbox"), model="claude-sonnet-5-5", client=FakeClient(ap_record()))
    r = t.run("please update our remittance account")
    assert r.ok
    assert r.record["requires_human_verification"] is True and r.record["urgency"] == "high"
    assert r.extra["invariants_fired"]


def test_ap_invariant_quiet_when_not_triggered():
    t = Triage(SkillSpec.load("ap_inbox"), model="claude-sonnet-5-5",
               client=FakeClient(ap_record(message_type="invoice", risk_flags=[], urgency="low")))
    r = t.run("invoice attached")
    assert r.record["requires_human_verification"] is False and r.extra["invariants_fired"] == []
