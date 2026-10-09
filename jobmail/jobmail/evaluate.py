"""Eval: score a triage skill against hand-labelled samples.

    python -m jobmail.evaluate job_inbox --model claude-sonnet-5 --env ../job-agent/.env
    python -m jobmail.evaluate job_inbox --model claude-sonnet-5-5 --repeats 3
    python -m jobmail.evaluate job_inbox --model claude-sonnet-5-5 --legacy   # the old request
    python -m jobmail.evaluate ap_inbox --model claude-sonnet-5-5
    python -m jobmail.evaluate --report                                        # eval/REPORT.md

Each sample carries an `expect` block. A list means any listed answer
passes (some emails have two defensible labels); `<field>_includes` means
the answer's list must contain those items; `has_key_date` checks that some
date was extracted. Results land in eval/results/ as JSON, one file per
skill/model/effort, and --report renders every result into one table.

The metric that matters most is recall on needs_reply (job_inbox) and
requires_human_verification (ap_inbox). A missed reply costs an
opportunity; a missed verification costs money. False alarms cost trust,
which is why precision is reported alongside.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from .classifier import ClaudeClassifier
from .config import use_utf8_io
from .demo import build_message
from .matcher import normalise_company
from .triage import SkillSpec, Triage, TriageResult, render_email, supports_effort

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {"job_inbox": ROOT / "samples" / "job_inbox.json", "ap_inbox": ROOT / "samples" / "ap_inbox.json"}
RESULTS = ROOT / "eval" / "results"

# $ per million tokens (input, output), list price.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0),
}

# What the classifier did on failure before this branch: quietly "nothing to do".
LEGACY_FALLBACK = {"is_job_related": True, "company": "", "role": "", "message_type": "other_job_related",
                   "sender_is_human": False, "needs_reply": False, "urgency": "low",
                   "summary": "Classification failed; review manually.", "action_needed": "", "key_dates": [],
                   "contact_name": ""}


class LegacyTriage(Triage):
    """The original request: a forced tool call to extract JSON, and a silent fallback."""

    def request(self, text: str) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": self.model, "max_tokens": 2048, "system": self.spec.instructions,
            "tools": [{"name": "record_classification", "description": "Record the classification.",
                       "input_schema": self.spec.schema}],
            "tool_choice": {"type": "tool", "name": "record_classification"},
            "messages": [{"role": "user", "content": text}],
        }
        if supports_effort(self.model):
            kw["thinking"] = {"type": "adaptive"}
            kw["output_config"] = {"effort": self.effort}
        return kw

    def run(self, text: str) -> TriageResult:
        try:
            resp = self.client.messages.create(**self.request(text))
        except Exception as exc:
            return TriageResult(dict(LEGACY_FALLBACK), ok=False, error=f"{type(exc).__name__}: {exc}"[:300],
                                model=self.model)
        for b in resp.content:
            if getattr(b, "type", None) == "tool_use":
                return TriageResult(dict(b.input), ok=True, model=self.model,
                                    input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        return TriageResult(dict(LEGACY_FALLBACK), ok=False, error="no tool_use block", model=self.model)


# ------------------------------------------------------------------ scoring
def score_item(expect: dict[str, Any], rec: dict[str, Any]) -> dict[str, bool]:
    out = {}
    for key, want in expect.items():
        if key.endswith("_includes"):
            out[key] = set(want) <= set(rec.get(key[: -len("_includes")]) or [])
        elif key == "has_key_date":
            out[key] = bool(rec.get("key_dates")) == want
        elif isinstance(want, bool):
            out[key] = rec.get(key) == want
        elif key in ("company", "vendor"):
            wants = want if isinstance(want, list) else [want]
            out[key] = normalise_company(rec.get(key) or "") in {normalise_company(w) for w in wants}
        elif isinstance(want, list):
            out[key] = str(rec.get(key, "")) in want
        else:
            out[key] = rec.get(key) == want
    return out


def _prf(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    """pairs of (expected, predicted) for a boolean field."""
    tp = sum(e and p for e, p in pairs)
    fp = sum((not e) and p for e, p in pairs)
    fn = sum(e and (not p) for e, p in pairs)
    return {"tp": tp, "fp": fp, "fn": fn, "positives": tp + fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def summarise(spec_name: str, items: list[dict[str, Any]], model: str) -> dict[str, Any]:
    fields: dict[str, list[bool]] = {}
    for it in items:
        for k, v in it["score"].items():
            fields.setdefault(k, []).append(v)
    key_bool = "needs_reply" if spec_name == "job_inbox" else "requires_human_verification"
    pairs = [(it["expect"][key_bool], bool(it["record"].get(key_bool)))
             for it in items if isinstance(it["expect"].get(key_bool), bool)]
    pin, pout = PRICES.get(model, (0, 0))
    tin = sum(it["input_tokens"] for it in items)
    tout = sum(it["output_tokens"] for it in items)
    lat = [it["latency_s"] for it in items if it["ok"]]
    return {
        "n": len(items),
        "fully_correct": sum(all(it["score"].values()) for it in items),
        "field_accuracy": {k: round(sum(v) / len(v), 3) for k, v in sorted(fields.items())},
        "critical_field": key_bool,
        "critical": _prf(pairs),
        "fallbacks": sum(not it["ok"] for it in items),
        "input_tokens": tin, "output_tokens": tout,
        "cost_usd": round(tin * pin / 1e6 + tout * pout / 1e6, 4),
        "cost_per_1k_messages_usd": round((tin * pin / 1e6 + tout * pout / 1e6) / len(items) * 1000, 2) if items else 0,
        "median_latency_s": round(statistics.median(lat), 2) if lat else None,
    }


# ------------------------------------------------------------------ running
def spec_hash(spec_name: str) -> str:
    import hashlib
    return hashlib.sha256((SkillSpec.load(spec_name).instructions +
                           json.dumps(SkillSpec.load(spec_name).schema, sort_keys=True)).encode()).hexdigest()[:8]


def run_eval(spec_name: str, model: str, effort: str, repeats: int, legacy: bool, workers: int = 4,
             tag: str = "") -> dict[str, Any]:
    from anthropic import Anthropic

    data = json.loads(SAMPLES[spec_name].read_text(encoding="utf-8"))
    labelled = [m for m in data["messages"] if "expect" in m]
    spec = SkillSpec.load(spec_name)
    client = Anthropic(max_retries=3)
    now = datetime.now().astimezone()

    def one(m: dict[str, Any], rep: int) -> dict[str, Any]:
        msg = build_message(m, 1000 + rep, data["owner_addr"], now)
        t0 = time.perf_counter()
        if legacy:
            r = LegacyTriage(spec, model=model, effort=effort, client=client).run(
                ClaudeClassifier("", model, data["owner_name"], client=client)._render(msg))
        elif spec_name == "job_inbox":
            clf = ClaudeClassifier("", model, data["owner_name"], effort=effort, client=client)
            clf.classify(msg)       # the production code path, end to end
            r = clf.last_result
        else:
            r = Triage(spec, model=model, effort=effort, client=client).run(render_email(msg, data["owner_name"]))
        return {"id": m["id"], "repeat": rep, "expect": m["expect"], "note": m.get("note", ""),
                "record": r.record, "ok": r.ok, "error": r.error,
                "score": score_item(m["expect"], r.record),
                "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                "latency_s": round(time.perf_counter() - t0, 2),
                "invariants_fired": r.extra.get("invariants_fired", [])}

    jobs = [(m, rep) for rep in range(repeats) for m in labelled]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(lambda j: one(*j), jobs))

    runs = [summarise(spec_name, [it for it in items if it["repeat"] == rep], model) for rep in range(repeats)]
    # consistency: did the same email get the same critical answer every time?
    key = runs[0]["critical_field"]
    by_id: dict[str, set] = {}
    for it in items:
        by_id.setdefault(it["id"], set()).add((it["record"].get("message_type"), it["record"].get(key)))
    return {
        "skill": spec_name, "model": model, "effort": effort, "legacy": legacy, "repeats": repeats,
        "tag": tag, "spec_sha": spec_hash(spec_name),
        "ran_at": now.isoformat(timespec="seconds"),
        "runs": runs,
        "stable_items": sum(len(v) == 1 for v in by_id.values()),
        "items": items,
    }


def result_path(res: dict[str, Any]) -> Path:
    tag = ("__" + res["tag"] if res.get("tag") else "") + ("__legacy" if res["legacy"] else "")
    return RESULTS / f"{res['skill']}__{res['model']}__{res['effort']}{tag}.json"


# ------------------------------------------------------------------ report
def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _range(vals: list[float | None]) -> str:
    v = [x for x in vals if x is not None]
    if not v:
        return "n/a"
    lo, hi = min(v), max(v)
    return _fmt_pct(lo) if lo == hi else f"{_fmt_pct(lo)}–{_fmt_pct(hi)}"


def build_report() -> str:
    files = sorted(RESULTS.glob("*.json"))
    results = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    out = ["# Eval report", "",
           "Generated by `python -m jobmail.evaluate --report` from every file in `eval/results/`. "
           "Ranges show min–max across repeats. Cost is list price for the sample set; "
           "per-1k is the same rate scaled to 1,000 messages.", ""]
    for skill in ("job_inbox", "ap_inbox"):
        rs = [r for r in results if r["skill"] == skill]
        if not rs:
            continue
        crit = rs[0]["runs"][0]["critical_field"]
        out += [f"## {skill}", "",
                f"| Config | n × runs | Fully correct | Type acc. | {crit} recall | {crit} precision | "
                "Fallbacks | Stable | $ / 1k msgs | Median latency |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(rs, key=lambda r: (r["legacy"], r.get("tag", ""), r["model"], r["effort"])):
            runs = r["runs"]
            n = runs[0]["n"]
            name = (f"{r['model']} / {r['effort']}" + (f" / spec {r['tag']}" if r.get("tag") else "")
                    + (" / **legacy request**" if r["legacy"] else ""))
            fc = [x["fully_correct"] / x["n"] for x in runs]
            ta = [x["field_accuracy"].get("message_type") for x in runs]
            out.append(
                f"| {name} | {n} × {len(runs)} | {_range(fc)} | {_range(ta)} | "
                f"{_range([x['critical']['recall'] for x in runs])} | {_range([x['critical']['precision'] for x in runs])} | "
                f"{sum(x['fallbacks'] for x in runs)} | {r['stable_items']}/{n} | "
                f"${statistics.mean(x['cost_per_1k_messages_usd'] for x in runs):.2f} | "
                f"{str(runs[0]['median_latency_s']) + 's' if runs[0]['median_latency_s'] else 'n/a'} |")
        out.append("")
        for r in sorted(rs, key=lambda r: (r["legacy"], r.get("tag", ""), r["model"], r["effort"])):
            misses = [it for it in r["items"] if not all(it["score"].values())]
            out += [f"### Misses: {r['model']} / {r['effort']}" + (f" / spec {r['tag']}" if r.get("tag") else "")
                    + (" (legacy)" if r["legacy"] else ""), ""]
            if not misses:
                out += ["None.", ""]
                continue
            errors = {it["error"][:160] for it in r["items"] if it["error"]}
            if len(errors) == 1 and all(it["error"] for it in r["items"]):
                out += [f"All {len(r['items'])} calls failed with the same error and fell back: "
                        f"`{errors.pop()}`", ""]
                continue
            seen = set()
            for it in misses:
                bad = [k for k, v in it["score"].items() if not v]
                sig = (it["id"], tuple(bad))
                if sig in seen:
                    continue
                seen.add(sig)
                times = sum(1 for x in misses if x["id"] == it["id"] and
                            [k for k, v in x["score"].items() if not v] == bad)
                got = {k: it["record"].get(k[: -len("_includes")] if k.endswith("_includes") else
                                           ("key_dates" if k == "has_key_date" else k)) for k in bad}
                want = {k: it["expect"][k] for k in bad}
                err = f" Error: `{it['error'][:120]}`" if it["error"] else ""
                out.append(f"- **{it['id']}** ({times}/{r['repeats']}) expected `{json.dumps(want)}`, "
                           f"got `{json.dumps(got, default=str)}`.{err}")
            out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-eval", description="Score a triage skill against labelled samples.")
    ap.add_argument("skill", nargs="?", choices=sorted(SAMPLES))
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--legacy", action="store_true", help="send the original forced-tool request")
    ap.add_argument("--tag", default="", help="label for this spec version, e.g. v1")
    ap.add_argument("--env", help=".env file holding ANTHROPIC_API_KEY")
    ap.add_argument("--report", action="store_true", help="render eval/REPORT.md from saved results")
    args = ap.parse_args(argv)
    use_utf8_io()

    if args.report:
        text = build_report()
        (ROOT / "eval" / "REPORT.md").write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    if not args.skill:
        ap.error("name a skill, or pass --report")

    from dotenv import load_dotenv
    load_dotenv(args.env, override=False)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        ap.error("ANTHROPIC_API_KEY not set (use --env)")

    n = sum("expect" in m for m in json.loads(SAMPLES[args.skill].read_text(encoding="utf-8"))["messages"])
    pin, pout = PRICES.get(args.model, (5, 25))
    est = n * args.repeats * (1500 * pin + 600 * pout) / 1e6
    print(f"{args.skill}: {n} samples × {args.repeats} on {args.model}/{args.effort}"
          f"{' (legacy request)' if args.legacy else ''}. Estimated cost ≈ ${est:.2f}")

    res = run_eval(args.skill, args.model, args.effort, args.repeats, args.legacy, tag=args.tag)
    RESULTS.mkdir(parents=True, exist_ok=True)
    result_path(res).write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for i, r in enumerate(res["runs"]):
        c = r["critical"]
        print(f"  run {i + 1}: fully correct {r['fully_correct']}/{r['n']}  "
              f"{r['critical_field']} recall={_fmt_pct(c['recall'])} precision={_fmt_pct(c['precision'])}  "
              f"fallbacks={r['fallbacks']}  ${r['cost_usd']}")
    print(f"  stable across repeats: {res['stable_items']}/{n}   -> {result_path(res).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
