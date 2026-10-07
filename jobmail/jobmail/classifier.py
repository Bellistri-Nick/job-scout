"""Classify inbound mail with Claude, returning a strict JSON structure.

Uses forced tool use so the response is always schema-conformant; the
"tool" is just a container for the classification.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .mail import ParsedMessage

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

CLASSIFICATION_TOOL: dict[str, Any] = {
    "name": "record_classification",
    "description": "Record the structured classification of one job-search email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_job_related": {
                "type": "boolean",
                "description": "True if this email concerns a job application, recruiter, interview, offer, or job search in any way.",
            },
            "company": {
                "type": "string",
                "description": "The hiring company (not the ATS vendor, recruiter agency, or job board). Empty string if unknown.",
            },
            "role": {
                "type": "string",
                "description": "Job title being discussed. Empty string if unknown.",
            },
            "message_type": {"type": "string", "enum": MESSAGE_TYPES},
            "sender_is_human": {
                "type": "boolean",
                "description": "True if written by a person (recruiter, hiring manager) rather than an automated system.",
            },
            "needs_reply": {
                "type": "boolean",
                "description": "True only if the recipient must personally respond (answer a question, pick a time, confirm, submit something). Auto-acks, rejections, and newsletters are false.",
            },
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary": {
                "type": "string",
                "description": "One sentence, plain language, written for the recipient. Max 200 characters.",
            },
            "action_needed": {
                "type": "string",
                "description": "If needs_reply, what exactly to do (e.g. 'Reply with availability for a 30-min call next week'). Empty if nothing.",
            },
            "key_dates": {
                "type": "array",
                "description": "Deadlines, interview times, or start dates mentioned. Use ISO 8601. Omit vague references.",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "ISO 8601 date or datetime"},
                        "description": {"type": "string"},
                    },
                    "required": ["date", "description"],
                },
            },
            "contact_name": {"type": "string", "description": "Name of the human sender, if any."},
        },
        "required": [
            "is_job_related",
            "company",
            "role",
            "message_type",
            "sender_is_human",
            "needs_reply",
            "urgency",
            "summary",
            "action_needed",
            "key_dates",
        ],
    },
}

SYSTEM_PROMPT = """You are an assistant that triages a job-seeker's dedicated job-search inbox.
You classify each email precisely and conservatively. You never draft replies.

Guidance:
- "company" is the employer, not the applicant-tracking system (Greenhouse, Lever, Workday, Ashby, iCIMS), not a staffing agency unless the agency itself is the employer, and not a job board.
- needs_reply is true only when a human response from the recipient is required. Automated confirmations, rejections, calendar auto-confirmations, and newsletters do not need replies.
- urgency: high = explicit deadline within ~48h or a live scheduling request from a human; medium = human asked something but no tight deadline; low = everything else.
- Prefer empty strings over guesses for company/role.
- Dates: convert relative dates using the email's Date header, which is provided. Only include dates you are confident about.
"""


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
        )

    def to_json(self) -> str:
        return json.dumps(self.raw or self.__dict__, default=str)


class Classifier(Protocol):
    def classify(self, msg: ParsedMessage) -> Classification: ...


# Effort is rejected by pre-4.6 models, which also don't think unless asked.
NO_EFFORT_MODELS = ("sonnet-4-5", "haiku-4-5", "opus-4-1", "haiku-3", "sonnet-3", "opus-3")


def supports_effort(model: str) -> bool:
    return not any(m in model for m in NO_EFFORT_MODELS)


class ClaudeClassifier:
    def __init__(self, api_key: str, model: str, owner_name: str = "", max_body_chars: int = 12000,
                 effort: str = "low"):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.owner_name = owner_name
        self.max_body_chars = max_body_chars
        self.effort = effort

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
        kwargs: dict[str, Any] = {}
        if supports_effort(self.model):
            # Triage against a fixed schema with forced tool use. It is a
            # classification route, which is exactly the shape that does not
            # repay deep reasoning, and this runs once per email forever.
            # Left unset, current models think at `high` by default and the
            # billed output tokens roughly triple for no gain in accuracy.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": self.effort}

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[CLASSIFICATION_TOOL],
            tool_choice={"type": "tool", "name": "record_classification"},
            messages=[{"role": "user", "content": self._render(msg)}],
            **kwargs,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "record_classification":
                return Classification.from_dict(dict(block.input))
        log.error("Claude returned no tool_use block; treating as not job related")
        return Classification.from_dict({"is_job_related": False, "message_type": "not_job_related"})
