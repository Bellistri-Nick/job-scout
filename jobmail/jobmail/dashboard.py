"""Read-only web dashboard. Run with `python -m jobmail.dashboard`."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import Config, use_utf8_io
from .db import OPEN_STAGES, STAGES, Database

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _rel(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return iso
    delta = datetime.now(timezone.utc) - dt
    s = int(delta.total_seconds())
    if s < 0:
        s = -s
        future = True
    else:
        future = False
    if s < 3600:
        txt = f"{max(1, s // 60)}m"
    elif s < 86400:
        txt = f"{s // 3600}h"
    else:
        txt = f"{s // 86400}d"
    return f"in {txt}" if future else f"{txt} ago"


def _fmt(iso: str | None, with_time: bool = True) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%b %d, %Y %H:%M" if with_time else "%b %d, %Y")
    except Exception:
        return iso


def _stamp(iso: str | None) -> str:
    """Wall-clock time of a run, for the header's last-poll line.

    The relative "15h ago" answers "is this current?"; the clock time answers
    "exactly when?", which is what you need when a poll looks like it was
    skipped. The year is dropped so the header stays on one line — the full
    form, year and all, is in the title attribute next to it.
    """
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        # Matches what the page's own JS writes on each poll, so the stamp does
        # not visibly reshape itself a moment after the page loads.
        return dt.strftime("%b %d, %H:%M")
    except Exception:
        return iso


def _gmail_link(message_id: str | None) -> str:
    """Deep-link to this exact thread in Gmail.

    Gmail's `rfc822msgid:` search operator resolves an RFC822 Message-ID to the
    thread, which is the shortest path from "this needs a reply" to the reply
    box. jobmail still sends nothing: it hands you off to Gmail and stops.
    Stored ids keep their angle brackets; the operator wants them stripped.
    """
    mid = (message_id or "").strip().strip("<>")
    if not mid:
        return ""
    return "https://mail.google.com/mail/u/0/#search/" + quote("rfc822msgid:" + mid, safe="")


TEMPLATES.env.filters["gmail"] = _gmail_link
TEMPLATES.env.filters["rel"] = _rel
TEMPLATES.env.filters["fmt"] = _fmt
TEMPLATES.env.filters["stamp"] = _stamp
TEMPLATES.env.filters["date"] = lambda v: _fmt(v, with_time=False)


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()
    app = FastAPI(title="jobmail", docs_url=None, redoc_url=None)

    def db() -> Database:
        return Database(cfg.db_path or cfg.data_dir / "jobmail.db")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        d = db()
        try:
            needs_reply = d.list_needs_reply()
            apps = d.list_applications()
            counts = d.stage_counts()
            stale = d.stale_applications(cfg.stale_after_days)
            upcoming = d.upcoming_key_dates(24 * 14, unalerted_only=False)
            last_run = d.get_state("last_run_at")
        finally:
            d.close()
        board = {s: [a for a in apps if a["stage"] == s] for s in STAGES}
        return TEMPLATES.TemplateResponse(request, "index.html", {
            "needs_reply": needs_reply, "board": board, "counts": counts, "stale": stale,
            "upcoming": upcoming, "last_run": last_run, "stages": STAGES,
            "open_stages": OPEN_STAGES, "total_open": sum(counts[s] for s in OPEN_STAGES),
            "stale_days": cfg.stale_after_days,
        })

    @app.get("/app/{app_id}", response_class=HTMLResponse)
    def application(request: Request, app_id: int):
        d = db()
        try:
            a = d.get_application(app_id)
            if not a:
                raise HTTPException(404)
            msgs = d.list_messages_for_application(app_id)
            events = d.list_events(app_id)
            dates = d.list_key_dates_for_application(app_id)
        finally:
            d.close()
        return TEMPLATES.TemplateResponse(request, "application.html", {
            "a": a, "msgs": msgs, "events": events, "dates": dates,
            "domains": json.loads(a["sender_domains"] or "[]"),
        })

    @app.get("/message/{msg_id}", response_class=HTMLResponse)
    def message(request: Request, msg_id: int):
        d = db()
        try:
            m = d.get_message(msg_id)
            if not m:
                raise HTTPException(404)
            a = d.get_application(m["application_id"]) if m["application_id"] else None
        finally:
            d.close()
        cls = json.loads(m["classification"]) if m["classification"] else {}
        return TEMPLATES.TemplateResponse(request, "message.html", {"m": m, "a": a, "cls": cls})

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request):
        d = db()
        try:
            stats = d.outcome_stats()
            funnel = d.funnel()
            by_month = d.applications_by_period("month")
            sources = d.top_sources()
            recent = d.list_applications()
            last_run = d.get_state("last_run_at")
        finally:
            d.close()
        return TEMPLATES.TemplateResponse(request, "metrics.html", {
            "stats": stats, "funnel": funnel, "by_month": by_month,
            "sources": sources, "stages": STAGES, "apps": recent,
            "last_run": last_run,
            "peak_month": max((n for _, n in by_month), default=1),
        })

    @app.get("/api/summary")
    def api_summary():
        d = db()
        try:
            counts = d.stage_counts()
            nr = d.list_needs_reply()
            return JSONResponse({
                "counts": counts,
                "needs_reply": len(nr),
                # a digest of what is on screen: the poller runs behind the page,
                # so the client compares this to decide whether a reload is worth
                # offering, instead of reloading on a timer and losing your scroll.
                "digest": ",".join(str(m["id"]) for m in nr) + "|" + str(sum(counts.values())),
                "total_open": sum(counts[s] for s in OPEN_STAGES),
                "stale": len(d.stale_applications(cfg.stale_after_days)),
                "last_run_at": d.get_state("last_run_at"),
            })
        finally:
            d.close()

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobmail-dashboard")
    ap.add_argument("--env")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args(argv)
    use_utf8_io()
    logging.basicConfig(level=logging.INFO)
    cfg = Config.load(args.env)
    uvicorn.run(create_app(cfg), host=args.host or cfg.dashboard_host, port=args.port or cfg.dashboard_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
