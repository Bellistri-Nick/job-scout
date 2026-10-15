"""Triage: one reusable skill that turns an unstructured message into a typed record.

A skill is a JSON spec, not code. It carries:

  name          what it is called
  instructions  the system prompt: who the reader is, what each label means
  schema        the JSON Schema Claude must answer in (structured outputs)
  fallback      the record to use when the call fails, written so a failure
                lands in front of a human instead of disappearing

The engine below runs any spec. jobmail's inbox classifier is the
`job_inbox` spec; the Finance AP inbox is `ap_inbox`. Same engine, same
guarantees, different schema. A new back-office inbox is a new JSON file.

Structured outputs (output_config.format) is what makes the answer
schema-valid. The first version forced a tool call to get JSON back, which
current models reject with a 400.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SPEC_DIR = Path(__file__).parent / "skills"

# Effort is rejected by pre-4.6 models, which also don't think unless asked.
NO_EFFORT_MODELS = ("sonnet-4-5", "haiku-4-5", "opus-4-1", "haiku-3", "sonnet-3", "opus-3")


def supports_effort(model: str) -> bool:
    return not any(m in model for m in NO_EFFORT_MODELS)


@dataclass
class SkillSpec:
    name: str
    description: str
    instructions: str
    schema: dict[str, Any]
    fallback: dict[str, Any]
    invariants: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, name_or_path: str | Path) -> "SkillSpec":
        p = Path(name_or_path)
        if not p.suffix:
            p = SPEC_DIR / f"{name_or_path}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(name=d["name"], description=d["description"], instructions=d["instructions"],
                   schema=d["schema"], fallback=d["fallback"], invariants=d.get("invariants", []))

    def enforce(self, record: dict[str, Any]) -> list[str]:
        """Apply the spec's invariants in code. Returns the ones that fired.

        The instructions ask the model to follow these rules too; this is the
        guarantee. Safety-critical fields are never left to the model alone.
        """
        fired = []
        for inv in self.invariants:
            cond = inv["when"]
            val = record.get(cond["field"])
            hit = (cond["contains"] in val) if "contains" in cond and isinstance(val, list) else \
                  (val == cond["equals"]) if "equals" in cond else False
            if hit:
                changed = {k: v for k, v in inv["set"].items() if record.get(k) != v}
                record.update(inv["set"])
                if changed:
                    fired.append(f"{cond['field']}~{cond.get('contains', cond.get('equals'))} -> set {changed}")
        return fired

    def validate(self, record: dict[str, Any]) -> list[str]:
        """Cheap belt-and-braces check of the fields the workflow branches on.

        Structured outputs already guarantees the shape. This catches the
        one thing it cannot: a spec edited by hand into something the
        downstream code does not expect.
        """
        problems = []
        props = self.schema.get("properties", {})
        for key in self.schema.get("required", []):
            if key not in record:
                problems.append(f"missing {key}")
        for key, val in record.items():
            enum = props.get(key, {}).get("enum")
            if enum and val not in enum:
                problems.append(f"{key}={val!r} not in enum")
        return problems


@dataclass
class TriageResult:
    record: dict[str, Any]
    ok: bool                      # False when the fallback was used
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Triage:
    """Run a SkillSpec against text with Claude."""

    def __init__(self, spec: SkillSpec, api_key: str | None = None, model: str = "claude-sonnet-5-5",
                 effort: str = "low", max_input_chars: int = 12000, client: Any = None):
        if client is None:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key) if api_key else Anthropic()
        self.client = client
        self.spec = spec
        self.model = model
        self.effort = effort
        self.max_input_chars = max_input_chars

    def request(self, text: str) -> dict[str, Any]:
        """The exact request body, exposed so tests and the eval can inspect it."""
        if len(text) > self.max_input_chars:
            text = text[: self.max_input_chars] + "\n[... truncated ...]"
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.spec.instructions,
            "messages": [{"role": "user", "content": text}],
            "output_config": {"format": {"type": "json_schema", "schema": self.spec.schema}},
        }
        if supports_effort(self.model):
            # A classification route against a fixed schema is exactly the
            # shape that does not repay deep reasoning, and it runs once per
            # message forever. Left unset, effort defaults to high or medium
            # and the billed output tokens roughly triple for no accuracy gain.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"]["effort"] = self.effort
        return kwargs

    def run(self, text: str) -> TriageResult:
        try:
            resp = self.client.messages.create(**self.request(text))
        except Exception as exc:
            log.error("%s: Claude call failed (%s: %s); using fallback", self.spec.name, type(exc).__name__, exc)
            return TriageResult(dict(self.spec.fallback), ok=False, error=f"{type(exc).__name__}: {exc}",
                                model=self.model)

        usage = getattr(resp, "usage", None)
        tokens = {"input_tokens": getattr(usage, "input_tokens", 0) or 0,
                  "output_tokens": getattr(usage, "output_tokens", 0) or 0}
        stop = getattr(resp, "stop_reason", None)
        text_out = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        try:
            record = json.loads(text_out)
        except (json.JSONDecodeError, TypeError):
            # refusal or max_tokens: the output is not guaranteed to match the schema
            log.error("%s: unparseable answer (stop_reason=%s); using fallback", self.spec.name, stop)
            return TriageResult(dict(self.spec.fallback), ok=False, error=f"unparseable (stop_reason={stop})",
                                model=self.model, **tokens)

        problems = self.spec.validate(record)
        if problems:
            log.error("%s: answer failed validation (%s); using fallback", self.spec.name, "; ".join(problems))
            return TriageResult(dict(self.spec.fallback), ok=False, error="invalid: " + "; ".join(problems),
                                model=self.model, **tokens)
        fired = self.spec.enforce(record)
        if fired:
            log.info("%s: invariants overrode the model: %s", self.spec.name, "; ".join(fired))
        return TriageResult(record, ok=True, model=self.model, extra={"invariants_fired": fired}, **tokens)


def main(argv: list[str] | None = None) -> int:
    """Run any skill from the terminal: python -m jobmail.triage ap_inbox message.eml"""
    import argparse

    from dotenv import load_dotenv

    from .config import use_utf8_io

    ap = argparse.ArgumentParser(prog="triage", description="Run a triage skill on one message.")
    ap.add_argument("skill", help="spec name in jobmail/skills/ or a path to a spec .json")
    ap.add_argument("file", help=".eml or plain-text file")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--env", help=".env file holding ANTHROPIC_API_KEY")
    args = ap.parse_args(argv)
    use_utf8_io()
    load_dotenv(args.env, override=False)

    raw = Path(args.file).read_bytes()
    if args.file.endswith(".eml"):
        from .mail import parse_message
        msg = parse_message(raw, 0, "cli")
        text = render_email(msg)
    else:
        text = raw.decode("utf-8", errors="replace")

    result = Triage(SkillSpec.load(args.skill), model=args.model, effort=args.effort).run(text)
    print(json.dumps(result.record, indent=2, ensure_ascii=False))
    if not result.ok:
        print(f"\n[fallback used: {result.error}]")
        return 1
    return 0


def render_email(msg: Any, owner_name: str = "") -> str:
    """The text a skill sees for one email: headers it needs, then the body."""
    owner = f"The recipient is {owner_name}.\n" if owner_name else ""
    return (f"{owner}From: {msg.from_name} <{msg.from_addr}>\nTo: {msg.to_addr}\n"
            f"Date: {msg.sent_at}\nSubject: {msg.subject}\n\n{msg.body_text}")


if __name__ == "__main__":
    raise SystemExit(main())
