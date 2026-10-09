from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from jobmail.matcher import normalise_company, roles_similar
from jobmail.obsidian import MARKER, ObsidianWriter
from jobmail.pipeline import Pipeline

from conftest import CapturingAlerter, StubClassifier, make_email


def test_normalise_company():
    assert normalise_company("Acme, Inc.") == "acme"
    assert normalise_company("ACME Corporation") == "acme"
    assert normalise_company("Stripe") == normalise_company("stripe inc")


def test_roles_similar():
    assert roles_similar("Senior Product Manager", "Sr. Product Manager")
    assert roles_similar("Product Manager, Growth", "Senior Product Manager - Growth")
    assert not roles_similar("Product Manager", "Software Engineer")
    assert roles_similar("", "anything")
    assert roles_similar("Senior Product Manager, AI Platform", "Senior PM, AI Platform")
    assert roles_similar("Staff Product Manager, Fleet Intelligence", "Staff PM - Fleet Intelligence")
    assert not roles_similar("Staff Product Manager, Clinical Data", "Group PM, AI Care Navigation")


def test_html_body_extraction():
    m = make_email(1, frm="a@b.com", subject="x", body="Hello <b>there</b>", msg_id="<h@b>", html=True)
    # multipart/alternative with text/plain present → plain wins
    assert "plain fallback" in m.body_text


def _pipeline(cfg, db, table):
    clf = StubClassifier(table)
    alerter = CapturingAlerter()
    obs = ObsidianWriter(cfg.obsidian_vault_path, "Applications")
    return Pipeline(cfg, db, clf, alerter, obs), clf, alerter


def test_end_to_end_flow(cfg, db, days_ago):
    table = {
        "Thank you for applying": {"company": "Acme Inc", "role": "Senior Product Manager",
                                   "message_type": "application_confirmation", "sender_is_human": False},
        "Interview availability": {"company": "Acme", "role": "Senior Product Manager",
                                   "message_type": "interview_request", "needs_reply": True, "urgency": "high",
                                   "action_needed": "Send availability for a 30-min screen",
                                   "key_dates": [{"date": (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(),
                                                  "description": "Reply deadline"}]},
        "Update on your application": {"company": "Acme", "role": "Senior Product Manager",
                                       "message_type": "rejection", "sender_is_human": False},
        "Weekly jobs digest": {"is_job_related": True, "message_type": "newsletter_or_job_alert"},
    }
    p, clf, alerter = _pipeline(cfg, db, table)

    # 1. auto-ack creates an application
    m1 = make_email(101, frm="no-reply@greenhouse.io", subject="Thank you for applying to Acme",
                    body="We received your application.", msg_id="<m1@gh>", when=days_ago(10))
    p.process_inbound(m1)
    apps = db.list_applications()
    assert len(apps) == 1 and apps[0]["company"] == "Acme Inc" and apps[0]["stage"] == "applied"
    app_id = apps[0]["id"]

    # 2. recruiter from company domain, different thread → matches by company, advances stage, needs reply
    m2 = make_email(102, frm="Jane Recruiter <jane@acme.com>", subject="Interview availability",
                    body="Can you send times?", msg_id="<m2@acme>", when=days_ago(2))
    p.process_inbound(m2)
    app = db.get_application(app_id)
    assert app["stage"] == "interviewing"
    assert app["next_action"].startswith("Send availability")
    assert len(db.list_needs_reply()) == 1
    assert "acme.com" in app["sender_domains"]

    # 3. newsletter is ignored (no application)
    m3 = make_email(103, frm="alerts@linkedin.com", subject="Weekly jobs digest", body="...", msg_id="<m3@li>")
    p.process_inbound(m3)
    assert len(db.list_applications()) == 1

    # 4. duplicates are skipped
    assert p.process_inbound(m2) == -1
    assert clf.calls == 3

    # 5. alerts: one reply-needed + one upcoming date; stale check runs but nothing stale
    p.send_alerts()
    subjects = [s for s, _ in alerter.sent]
    assert any("Reply needed" in s for s in subjects)
    assert any("Upcoming" in s for s in subjects)
    assert len(db.list_unalerted_needs_reply()) == 0
    p.send_alerts()  # idempotent
    assert len(alerter.sent) == 2

    # 6. outbound reply from Sent resolves needs_reply
    out = make_email(7, frm="me@gmail.com", to="jane@acme.com", subject="Re: Interview availability",
                     body="Tuesday works", msg_id="<o1@mf>", in_reply_to="<m2@acme>", folder="Sent", when=days_ago(1))
    p.process_outbound(out)
    assert len(db.list_needs_reply()) == 0
    assert db.get_application(app_id)["next_action"] is None
    msgs = db.list_messages_for_application(app_id)
    assert [m["direction"] for m in msgs] == ["inbound", "inbound", "outbound"]

    # 7. rejection via thread reference closes it
    m4 = make_email(104, frm="no-reply@greenhouse.io", subject="Update on your application",
                    body="Unfortunately...", msg_id="<m4@gh>", references=["<m1@gh>"])
    p.process_inbound(m4)
    assert db.get_application(app_id)["stage"] == "rejected"
    assert db.get_application(app_id)["closed_at"] is not None

    # 8. obsidian mirror preserves user notes
    p.mirror_to_obsidian()
    note = next(pth for pth in (cfg.obsidian_vault_path / "Applications").glob("Acme*.md"))
    text = note.read_text(encoding="utf-8")
    assert "jobmail_id: 1" in text and "stage: rejected" in text and MARKER in text
    assert "Tuesday works" in text
    note.write_text(text + "\nMy own thoughts here.\n", encoding="utf-8")
    p.mirror_to_obsidian(all_apps=True)
    text2 = note.read_text(encoding="utf-8")
    assert "My own thoughts here." in text2 and text2.count(MARKER) == 1
    assert (cfg.obsidian_vault_path / "Applications" / "_Index.md").exists()


def test_stale_alert_once_per_day(cfg, db, days_ago):
    p, _, alerter = _pipeline(cfg, db, {"Applied": {"company": "OldCo", "role": "PM",
                                                    "message_type": "application_confirmation"}})
    m = make_email(1, frm="x@oldco.com", subject="Applied", body="", msg_id="<s@x>", when=days_ago(30))
    p.process_inbound(m)
    p.send_alerts()
    assert any("stale" in s for s, _ in alerter.sent)
    n = len(alerter.sent)
    p.send_alerts()
    assert len(alerter.sent) == n


def test_no_stage_regression(cfg, db):
    table = {"Offer": {"company": "Zed", "role": "PM", "message_type": "offer"},
             "Thanks": {"company": "Zed", "role": "PM", "message_type": "application_confirmation"}}
    p, _, _ = _pipeline(cfg, db, table)
    p.process_inbound(make_email(1, frm="hr@zed.com", subject="Offer", body="", msg_id="<1@z>"))
    p.process_inbound(make_email(2, frm="no-reply@lever.co", subject="Thanks for applying", body="", msg_id="<2@z>"))
    assert db.list_applications()[0]["stage"] == "offer"


def test_dashboard_renders(cfg, db):
    from fastapi.testclient import TestClient
    from jobmail.dashboard import create_app

    p, _, _ = _pipeline(cfg, db, {"Hi": {"company": "Dash Co", "role": "PM", "message_type": "recruiter_outreach",
                                          "needs_reply": True, "urgency": "medium", "action_needed": "Reply to Sam"}})
    p.process_inbound(make_email(1, frm="Sam <sam@dashco.com>", subject="Hi from Dash Co", body="Let's chat", msg_id="<d@1>"))
    db.close()
    client = TestClient(create_app(cfg))
    r = client.get("/")
    assert r.status_code == 200 and "Dash Co" in r.text and "Reply to Sam" in r.text
    r = client.get("/app/1")
    assert r.status_code == 200 and "Let's chat" not in r.text and "Hi from Dash Co" in r.text
    r = client.get("/message/1")
    assert r.status_code == 200 and "Let&#39;s chat" in r.text or "Let's chat" in r.text
    assert client.get("/api/summary").json()["needs_reply"] == 1
    assert client.get("/app/999").status_code == 404


def test_dashboard_stamps_the_last_run(cfg, db):
    """The header carries a wall-clock time, not just "15h ago".

    The relative age alone cannot tell you whether the 08:00 poll fired or the
    timer skipped it, which is the question the header exists to answer.
    """
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient

    from jobmail.dashboard import _stamp, create_app

    ran_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db.set_state("last_run_at", ran_at.isoformat())
    db.close()
    client = TestClient(create_app(cfg))
    clock = _stamp(ran_at.isoformat())
    assert clock != "never" and ran_at.astimezone().strftime("%H:%M") in clock
    for path in ("/", "/metrics"):
        body = client.get(path).text
        assert f"last poll {clock}" in body, path
        assert "3h ago" in body, path
    assert client.get("/api/summary").json()["last_run_at"] == ran_at.isoformat()


def test_dashboard_stamp_survives_a_db_that_never_polled(cfg, db):
    from fastapi.testclient import TestClient

    from jobmail.dashboard import create_app

    db.close()
    client = TestClient(create_app(cfg))
    for path in ("/", "/metrics"):
        r = client.get(path)
        assert r.status_code == 200 and "last poll never" in r.text, path


def test_effort_is_only_sent_to_models_that_accept_it():
    """Pre-4.6 models 400 on output_config.effort, so it must be omitted for them."""
    from jobmail.classifier import supports_effort

    assert supports_effort("claude-sonnet-5")
    assert supports_effort("claude-opus-5")
    assert not supports_effort("claude-haiku-4-5-20251001")
    assert not supports_effort("claude-sonnet-4-5")


def test_classifier_asks_for_low_effort(monkeypatch):
    from jobmail.classifier import ClaudeClassifier
    from conftest import make_email

    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            import json as _json
            rec = {"is_job_related": True, "company": "Acme", "role": "", "message_type": "rejection",
                   "sender_is_human": False, "needs_reply": False, "urgency": "low", "summary": "s",
                   "action_needed": "", "key_dates": [], "contact_name": ""}
            class R:
                content = [type("B", (), {"type": "text", "text": _json.dumps(rec)})()]
                usage = None
                stop_reason = "end_turn"
            return R()

    class FakeAnthropic:
        def __init__(self, **kw): self.messages = FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)

    c = ClaudeClassifier("k", "claude-sonnet-5", "Sam")
    c.classify(make_email(1, frm="a@b.com", subject="s", body="b", msg_id="<x@y>"))
    assert captured["output_config"]["effort"] == "low"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["thinking"] == {"type": "adaptive"}

    captured.clear()
    ClaudeClassifier("k", "claude-haiku-4-5-20251001", "Sam").classify(
        make_email(1, frm="a@b.com", subject="s", body="b", msg_id="<x@y>"))
    assert "effort" not in captured["output_config"] and "thinking" not in captured


def test_unusable_vault_does_not_stop_the_poller(cfg, db, tmp_path, caplog):
    """Obsidian is a mirror. A bad vault path must cost notes, never mail."""
    from jobmail.obsidian import ObsidianWriter

    # a path that cannot be created: a file where a directory needs to be
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    writer = ObsidianWriter(blocker / "vault", "Applications")   # must not raise here

    p, _, _ = _pipeline(cfg, db, {"Applied": {"company": "Acme", "role": "PM",
                                              "message_type": "application_confirmation"}})
    p.obsidian = writer
    p.process_inbound(make_email(1, frm="hr@acme.com", subject="Applied", body="x", msg_id="<a@1>"))
    p.mirror_to_obsidian()   # must not raise either

    assert len(db.list_applications()) == 1        # the mail still landed
    assert "Obsidian mirror is failing" in caplog.text


def test_verbose_does_not_enable_sdk_wire_logging(monkeypatch, tmp_path):
    """-v must raise jobmail's logger without making the Anthropic SDK dump requests."""
    import logging as _logging
    from jobmail import pipeline

    monkeypatch.setattr(pipeline, "build", lambda cfg: (_ for _ in ()).throw(SystemExit(0)))
    for name in ("anthropic", "httpx", "jobmail"):
        _logging.getLogger(name).setLevel(_logging.NOTSET)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=k\nIMAP_USER=u\nIMAP_PASSWORD=p\n", encoding="utf-8")

    try:
        pipeline.main(["--env", str(env), "--dry-run", "-v"])
    except SystemExit:
        pass

    assert _logging.getLogger("jobmail").level == _logging.DEBUG
    assert _logging.getLogger("anthropic").level == _logging.WARNING
    assert _logging.getLogger("httpx").level == _logging.WARNING


def test_gmail_deep_link_encoding():
    """The Message-ID must survive into a working Gmail search URL."""
    from jobmail.dashboard import _gmail_link

    url = _gmail_link("<CAF=a+b/c@mail.gmail.com>")
    assert url.startswith("https://mail.google.com/mail/u/0/#search/rfc822msgid%3A")
    assert "%2B" in url and "%2F" in url and "%40" in url   # + / @ all escaped
    assert "<" not in url and ">" not in url                # brackets stripped
    assert _gmail_link("") == "" and _gmail_link(None) == ""


def test_needs_reply_row_offers_both_actions(cfg, db):
    """Every reply-needed row must reach the email and Gmail without hunting."""
    from fastapi.testclient import TestClient
    from jobmail.dashboard import create_app

    p, _, _ = _pipeline(cfg, db, {"Hi": {"company": "Dash Co", "role": "PM",
                                          "message_type": "recruiter_outreach",
                                          "needs_reply": True, "urgency": "high",
                                          "action_needed": "Reply to Sam"}})
    p.process_inbound(make_email(1, frm="Sam <sam@dashco.com>", subject="Hi from Dash Co",
                                 body="Let's chat", msg_id="<abc+1@mail.gmail.com>"))
    db.close()
    r = TestClient(create_app(cfg)).get("/")
    assert r.status_code == 200
    assert 'data-href="/message/1"' in r.text          # the row itself is clickable
    assert ">Read email</a>" in r.text
    assert "rfc822msgid%3Aabc%2B1%40mail.gmail.com" in r.text
    assert 'target="_blank"' in r.text


def test_source_labels_the_channel():
    from jobmail.matcher import source_for

    assert source_for("us.greenhouse-mail.io") == "Greenhouse"
    assert source_for("hire.lever.co") == "Lever"
    assert source_for("acme.myworkday.com") == "Workday"
    assert source_for("careers.acme.com") == "Direct"     # company wrote directly
    assert source_for("gmail.com") is None                # personal, not a channel
    assert source_for("") is None


def test_funnel_remembers_progress_after_rejection(cfg, db):
    """A rejected application must still count as having interviewed."""
    table = {"Applied": {"company": "Acme", "role": "PM", "message_type": "application_confirmation"},
             "Interview": {"company": "Acme", "role": "PM", "message_type": "interview_request"},
             "Update": {"company": "Acme", "role": "PM", "message_type": "rejection"}}
    p, _, _ = _pipeline(cfg, db, table)
    p.process_inbound(make_email(1, frm="no-reply@greenhouse.io", subject="Applied", body="", msg_id="<1@a>"))
    p.process_inbound(make_email(2, frm="jane@acme.com", subject="Interview", body="", msg_id="<2@a>"))
    p.process_inbound(make_email(3, frm="jane@acme.com", subject="Update", body="", msg_id="<3@a>"))

    assert db.list_applications()[0]["stage"] == "rejected"
    f = db.funnel()
    assert f["applied"] == 1 and f["interviewing"] == 1 and f["rejected"] == 1

    o = db.outcome_stats()
    assert o["total"] == 1 and o["responded"] == 1 and o["rejected"] == 1
    assert o["response_rate"] == 100
    assert db.top_sources()[0][0] == "Greenhouse"


def test_metrics_page_renders(cfg, db, days_ago):
    from fastapi.testclient import TestClient
    from jobmail.dashboard import create_app

    table = {"Applied": {"company": "Acme", "role": "PM", "message_type": "application_confirmation"},
             "Interview": {"company": "Acme", "role": "PM", "message_type": "interview_request"},
             "Thanks": {"company": "Zed", "role": "PM", "message_type": "application_confirmation"}}
    p, _, _ = _pipeline(cfg, db, table)
    p.process_inbound(make_email(1, frm="no-reply@greenhouse.io", subject="Applied", body="",
                                 msg_id="<1@a>", when=days_ago(20)))
    p.process_inbound(make_email(2, frm="jane@acme.com", subject="Interview", body="",
                                 msg_id="<2@a>", when=days_ago(12)))
    p.process_inbound(make_email(3, frm="no-reply@lever.co", subject="Thanks for applying", body="",
                                 msg_id="<3@z>", when=days_ago(5)))
    db.close()

    r = TestClient(create_app(cfg)).get("/metrics")
    assert r.status_code == 200
    assert "response rate" in r.text and "Funnel" in r.text
    assert "Greenhouse" in r.text and "Lever" in r.text     # sources resolved
    assert "at least as far as" in r.text                   # the funnel caveat is stated
    assert 'href="/metrics"' in TestClient(create_app(cfg)).get("/").text   # reachable from nav


def test_funnel_never_widens(cfg, db):
    """A funnel that grows as it descends is nonsense on a chart."""
    table = {"Applied": {"company": "Acme", "role": "PM", "message_type": "application_confirmation"},
             "Interview": {"company": "Acme", "role": "PM", "message_type": "interview_request"}}
    p, _, _ = _pipeline(cfg, db, table)
    # jumps applied -> interviewing, never passing through screening
    p.process_inbound(make_email(1, frm="no-reply@greenhouse.io", subject="Applied", body="", msg_id="<1@a>"))
    p.process_inbound(make_email(2, frm="jane@acme.com", subject="Interview", body="", msg_id="<2@a>"))

    f = db.funnel()
    order = ["applied", "screening", "interviewing", "assessment", "offer"]
    counts = [f[s] for s in order]
    assert counts == sorted(counts, reverse=True), counts
    assert f["screening"] == 1      # counted, even though it skipped that stage
    assert f["offer"] == 0


def test_opens_a_database_from_the_previous_version(tmp_path):
    """The upgrade path, which fresh-DB tests never exercise."""
    import sqlite3
    from jobmail.db import Database

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL,
             company_key TEXT NOT NULL, role TEXT, stage TEXT NOT NULL DEFAULT 'applied',
             source TEXT, sender_domains TEXT NOT NULL DEFAULT '[]', applied_at TEXT,
             last_activity_at TEXT, next_action TEXT, next_action_due TEXT,
             created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT);
           CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER,
             kind TEXT NOT NULL, detail TEXT, at TEXT NOT NULL);"""
    )
    old.execute("INSERT INTO applications (company, company_key, stage, created_at, updated_at)"
                " VALUES ('Acme','acme','interviewing','2026-01-01','2026-01-01')")
    old.execute("INSERT INTO events (application_id, kind, detail, at)"
                " VALUES (1,'stage_change','applied \u2192 interviewing (interview_request)','2026-01-02')")
    old.commit()
    old.close()

    d = Database(path)                      # must not raise
    try:
        cols = {r["name"] for r in d.conn.execute("PRAGMA table_info(events)")}
        assert "to_stage" in cols
        # the pre-existing transition is recovered, not silently lost
        assert d.funnel()["interviewing"] == 1
        idx = {r[1] for r in d.conn.execute("PRAGMA index_list(events)")}
        assert "idx_events_to_stage" in idx
        Database(path).close()              # and re-opening is idempotent
    finally:
        d.close()


def test_latin1_stream_would_crash_on_real_mail_text():
    """Demonstrates the failure this guards against, so it can't quietly return."""
    import io

    latin1 = io.TextIOWrapper(io.BytesIO(), encoding="latin-1", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        latin1.write("Priya \u2014 can you confirm Thursday?")   # em dash from a real email
        latin1.flush()

    # what use_utf8_io does to that same stream
    latin1.reconfigure(encoding="utf-8", errors="replace")
    latin1.write("Priya \u2014 caf\u00e9 \U0001f600 \u4f60\u597d")
    latin1.flush()


def test_use_utf8_io_is_safe_to_call_anywhere():
    import sys
    from jobmail.config import use_utf8_io

    use_utf8_io()
    use_utf8_io()                        # idempotent
    for stream in (sys.stdout, sys.stderr):
        enc = getattr(stream, "encoding", "utf-8")
        assert enc.lower().replace("-", "") == "utf8" or not hasattr(stream, "reconfigure")


def test_pipeline_logs_non_ascii_summaries_without_dying(cfg, db, caplog):
    """A curly quote in a recruiter's summary must not take down the poll."""
    p, _, _ = _pipeline(cfg, db, {"Interview": {
        "company": "Caf\u00e9 Co", "role": "PM", "message_type": "interview_request",
        "needs_reply": True, "urgency": "high",
        "summary": "Priya \u2014 \u201ccan you confirm Thursday?\u201d \U0001f600",
        "action_needed": "Reply \u2192 with times"}})
    assert p.process_inbound(make_email(1, frm="p@cafe.co", subject="Interview",
                                        body="hi", msg_id="<u@1>")) > 0
    assert db.list_applications()[0]["company"] == "Caf\u00e9 Co"


def _alerted(cfg, db, table, subject, **kw):
    p, _, alerter = _pipeline(cfg, db, table)
    p.process_inbound(make_email(1, frm="hr@acme.com", subject=subject, body="b",
                                 msg_id="<x@1>", **kw))
    p.send_alerts()
    return alerter.sent


def test_offer_alerts_even_though_it_needs_no_reply(cfg, db):
    """The bug: an offer asks nothing of you, so needs_reply was 0 and the phone stayed silent."""
    sent = _alerted(cfg, db, {"Offer": {
        "company": "Acme", "role": "Staff PM", "message_type": "offer",
        "needs_reply": False, "summary": "Written offer attached."}}, "Offer of employment")
    assert len(sent) == 1
    subject, body = sent[0]
    assert "Offer" in subject and "Reply needed" not in subject
    assert "Acme" in body


def test_interview_invite_with_a_booking_link_alerts(cfg, db):
    sent = _alerted(cfg, db, {"Book": {
        "company": "Acme", "role": "PM", "message_type": "interview_request",
        "needs_reply": False, "summary": "Pick a slot on the link."}}, "Book your interview")
    assert len(sent) == 1 and "Interview request" in sent[0][0]


def test_noise_still_stays_silent(cfg, db):
    for subj, t in (("Thanks for applying", "application_confirmation"),
                    ("Weekly jobs digest", "newsletter_or_job_alert")):
        p, _, alerter = _pipeline(cfg, db, {subj: {"company": "Acme", "message_type": t,
                                                   "needs_reply": False}})
        p.process_inbound(make_email(hash(subj) % 999, frm="no-reply@greenhouse.io",
                                     subject=subj, body="", msg_id=f"<{t}@1>"))
        p.send_alerts()
        assert alerter.sent == [], f"{t} should not alert"


def test_personal_mail_cannot_page_you(cfg, db):
    """A needs_reply on mail the classifier says is not job related must not alert."""
    sent = _alerted(cfg, db, {"iphone": {
        "is_job_related": False, "message_type": "not_job_related",
        "needs_reply": True, "urgency": "high", "summary": "Leo wants an iPhone."}},
        "hi get me that iphone")
    assert sent == []


def test_alert_types_are_configurable(cfg, db):
    cfg.alert_on_types = frozenset({"offer"})      # only offers
    sent = _alerted(cfg, db, {"Recruiter": {
        "company": "Acme", "message_type": "recruiter_outreach",
        "needs_reply": False, "summary": "Saw your profile."}}, "Recruiter note")
    assert sent == []


def test_needs_reply_always_alerts_whatever_the_type(cfg, db):
    cfg.alert_on_types = frozenset()               # nothing by type
    sent = _alerted(cfg, db, {"Question": {
        "company": "Acme", "message_type": "other_job_related",
        "needs_reply": True, "urgency": "high", "summary": "Can you confirm?",
        "action_needed": "Confirm Thursday"}}, "Quick question")
    assert len(sent) == 1 and "Reply needed" in sent[0][0]


def test_an_alert_is_sent_once(cfg, db):
    p, _, alerter = _pipeline(cfg, db, {"Offer": {
        "company": "Acme", "message_type": "offer", "needs_reply": False}})
    p.process_inbound(make_email(1, frm="hr@acme.com", subject="Offer", body="", msg_id="<o@1>"))
    p.send_alerts(); p.send_alerts()
    assert len(alerter.sent) == 1


def test_new_role_after_rejection_is_a_new_application(cfg, db, days_ago):
    """A recruiter returning with a different role must not reopen the rejected application."""
    p, _, _ = _pipeline(cfg, db, {
        "received": {"company": "Halcyon Health", "role": "Staff Product Manager, Clinical Data",
                     "message_type": "application_confirmation"},
        "not moving forward": {"company": "Halcyon Health", "role": "Staff Product Manager, Clinical Data",
                               "message_type": "rejection"},
        "new opening": {"company": "Halcyon Health", "role": "Group Product Manager, AI Care Navigation",
                        "message_type": "recruiter_outreach", "needs_reply": True},
    })
    p.process_inbound(make_email(1, frm="no-reply@greenhouse.io", subject="We received your application",
                                 body=".", msg_id="<a1@gh>", when=days_ago(20)))
    p.process_inbound(make_email(2, frm="no-reply@greenhouse.io", subject="We are not moving forward",
                                 body=".", msg_id="<a2@gh>", when=days_ago(10)))
    p.process_inbound(make_email(3, frm="sofia@halcyon.example", subject="A new opening",
                                 body=".", msg_id="<a3@h>", when=days_ago(1)))
    apps = {a["role"]: a for a in db.list_applications()}
    assert apps["Staff Product Manager, Clinical Data"]["stage"] == "rejected"
    assert apps["Group Product Manager, AI Care Navigation"]["stage"] == "screening"
    assert p.last_match.how == "created(new role)"


def test_open_asks_clear_when_the_conversation_moves_on(cfg, db, days_ago):
    """Needs-reply used to clear only on an in-thread reply. Two other ways now count."""
    p, _, _ = _pipeline(cfg, db, {
        "applied": {"company": "Quarry", "role": "PM", "message_type": "application_confirmation"},
        "case study": {"company": "Quarry", "role": "PM", "message_type": "assessment", "needs_reply": True},
        "checking in": {"company": "Quarry", "role": "PM", "message_type": "follow_up", "needs_reply": True},
    })
    p.process_inbound(make_email(1, frm="x@lever.co", subject="applied", body=".", msg_id="<q1@l>", when=days_ago(9)))
    p.process_inbound(make_email(2, frm="e@quarry.example", subject="case study", body=".", msg_id="<q2@q>",
                                 when=days_ago(5)))
    p.process_inbound(make_email(3, frm="e@quarry.example", subject="checking in", body=".", msg_id="<q3@q>",
                                 when=days_ago(2)))
    # 1. the follow-up takes over the original ask: one open item, not two
    assert [r["subject"] for r in db.list_needs_reply()] == ["checking in"]
    # 2. a reply in a NEW thread to the same company still resolves it
    p.process_outbound(make_email(9, frm="me@gmail.com", to="e@quarry.example", subject="Case study attached",
                                  body=".", msg_id="<o9@me>", folder="Sent", when=days_ago(1)))
    assert db.list_needs_reply() == []


def test_stage_events_carry_the_email_date_not_the_poll_time(cfg, db, days_ago):
    """Backfill processes weeks of mail at once; the funnel's timing must not collapse to today."""
    p, _, _ = _pipeline(cfg, db, {
        "applied": {"company": "Acme", "role": "PM", "message_type": "application_confirmation"},
        "screen": {"company": "Acme", "role": "PM", "message_type": "interview_request"},
    })
    p.process_inbound(make_email(1, frm="x@lever.co", subject="applied", body=".", msg_id="<t1@l>", when=days_ago(30)))
    p.process_inbound(make_email(2, frm="r@acme.example", subject="screen", body=".", msg_id="<t2@a>", when=days_ago(20)))
    ev = [e for e in db.list_events(1) if e["kind"] == "stage_change"]
    assert ev and ev[0]["at"][:10] == days_ago(20).date().isoformat()
    assert 9 <= db.outcome_stats()["median_days_to_response"] <= 11
