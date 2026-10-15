"""Classify inbound mail with Claude, returning a strict JSON structure.

The schema and instructions live in skills/job_inbox.json and run through the
reusable Triage engine (triage.py), which uses structured outputs so every
answer is schema-valid. The first version forced a tool call to get JSON
back; current models reject forced tool use with a 400, and a broad except
turned that into silent "nothing needs a reply". See the eval for the case.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .mail import ParsedMessage
from .triage import SkillSpec, Triage, TriageResult, supports_effort  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

MESSAGE_TYPES = [
    "application_confirmation",  # "thanks for applying" auto-ack
    "recruiter_outreach",        # a human reaching out first
    "interview_request",         # asking for availability / inviting to interview
    "scheduling",                # confirmations, reschedules, calendar invites
    "assessment",                # take-home, coding test, case study
    "follow_up",                 # status update or check-in from them
    "rejection",
    "offer",
    "reference_or_background",   # background check, reference request
    "newsletter_or_job_alert",   # job board digests, LinkedIn alerts
    "other_job_related",
    "not_job_related",
]

STAGE_FOR_TYPE = {
    "application_confirmation": "applied",
    "recruiter_outreach": "screening",
    "interview_request": "interviewing",
    "scheduling": "interviewing",
    "assessment": "assessment",
    "offer": "offer",
    "rejection": "rejected",
    "reference_or_background": "offer",
}


@dataclass
class Classification:
    is_job_related: bool
    company: str
    role: str
    message_type: str
    sender_is_human: bool
    needs_reply: bool
    urgency: str
    summary: str
    action_needed: str
    key_dates: list[dict[str, str]] = field(default_factory=list)
    contact_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    failed: bool = False          # True when the skill fell back instead of classifying
    suspected_fraud: bool = False
    fraud_signals: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Classification":
        return cls(
            is_job_related=bool(d.get("is_job_related", False)),
            company=(d.get("company") or "").strip(),
            role=(d.get("role") or "").strip(),
            message_type=d.get("message_type") or "other_job_related",
            sender_is_human=bool(d.get("sender_is_human", False)),
            needs_reply=bool(d.get("needs_reply", False)),
            urgency=d.get("urgency") or "low",
            summary=(d.get("summary") or "").strip(),
            action_needed=(d.get("action_needed") or "").strip(),
            key_dates=[
                {"date": kd.get("date", ""), "description": kd.get("description", "")}
                for kd in (d.get("key_dates") or [])
                if kd.get("date")
            ],
            contact_name=(d.get("contact_name") or "").strip(),
            raw=d,
            suspected_fraud=bool(d.get("suspected_fraud", False)),
            fraud_signals=list(d.get("fraud_signals") or []),
        )

    def flag_fraud(self, signal: str, action: str) -> None:
        """Mark as suspected fraud after the model answered, keeping the record in step."""
        self.suspected_fraud = True
        if signal not in self.fraud_signals:
            self.fraud_signals.append(signal)
        self.needs_reply, self.urgency, self.action_needed = False, "high", action
        self.raw = {**self.raw, "suspected_fraud": True, "fraud_signals": self.fraud_signals,
                    "needs_reply": False, "urgency": "high", "action_needed": action}

    def to_json(self) -> str:
        return json.dumps(self.raw or self.__dict__, default=str)


class Classifier(Protocol):
    def classify(self, msg: ParsedMessage) -> Classification: ...


class ClaudeClassifier:
    """jobmail's classifier: the `job_inbox` triage skill, adapted to Classification.

    All the Claude-facing work (request shape, structured outputs, effort,
    fallback on failure) lives in the reusable Triage engine. This class only
    renders an email into text and maps the record onto jobmail's dataclass.
    """

    def __init__(self, api_key: str, model: str, owner_name: str = "", max_body_chars: int = 12000,
                 effort: str = "low", client: Any = None, spec: SkillSpec | None = None):
        self.spec = spec or SkillSpec.load("job_inbox")
        self.triage = Triage(self.spec, api_key=api_key, model=model, effort=effort,
                             max_input_chars=max_body_chars + 500, client=client)
        self.model = model
        self.owner_name = owner_name
        self.max_body_chars = max_body_chars
        self.last_result: TriageResult | None = None

    def _render(self, msg: ParsedMessage) -> str:
        body = msg.body_text
        if len(body) > self.max_body_chars:
            body = body[: self.max_body_chars] + "\n[... truncated ...]"
        owner = f"The recipient (the job seeker) is {self.owner_name}.\n" if self.owner_name else ""
        return (
            f"{owner}"
            f"From: {msg.from_name} <{msg.from_addr}>\n"
            f"To: {msg.to_addr}\n"
            f"Date: {msg.sent_at}\n"
            f"Subject: {msg.subject}\n\n"
            f"{body}"
        )

    def classify(self, msg: ParsedMessage) -> Classification:
        self.last_result = self.triage.run(self._render(msg))
        cls = Classification.from_dict(self.last_result.record)
        cls.failed = not self.last_result.ok
        return cls
