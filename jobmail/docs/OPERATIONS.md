# jobmail

A read-only agent for a job-search mailbox. It polls Gmail over IMAP, has Claude classify each message, keeps every application and its correspondence in SQLite, pings you on Telegram and by email when something needs a reply, mirrors the history into an Obsidian vault, and serves a private dashboard. It never sends mail on your behalf and never drafts replies.

The dashboard is read-only by design, but not static: filter everything at once from one box
(`/` focuses it), click a stage tile to isolate that column, and a background check against
`/api/summary` every 30s offers a refresh when the poller finds something rather than
reloading under you and losing your place.

```
Gmail (IMAP)     ──► fetch ──► Claude classify ──► match to application ──► SQLite
                                                                          ├──► Telegram + email alerts
                                                                          ├──► Obsidian notes
                                                                          └──► dashboard (FastAPI, read-only)
```

## What it does on each poll (hourly, 8am-8pm every day by default)

1. Fetches new messages from `INBOX` (by UID, so nothing is reprocessed) and from your Sent folder. If the server ever reissues UIDs (a `UIDVALIDITY` change), the folder is rescanned and messages already on file are recognised by their `Message-ID`, so a reset costs no Claude calls.
2. Sends each inbound message to Claude with a strict JSON schema: company, role, message type, whether *you* need to reply, urgency, a one-line summary, the concrete action, and any dates.
3. Links it to an application: threading headers first, then company name, then sender domain (ignoring shared ATS/job-board domains). Creates a new application when nothing matches.
4. Advances the stage automatically (`applied → screening → interviewing → assessment → offer`, or `rejected`). It only moves forward; it won't demote an application.
5. Records outbound mail from your Sent folder and marks the message it replies to as handled, so a "needs reply" clears itself once you answer from Gmail on any device.
6. Alerts on: anything needing a reply, plus offers, interview requests, scheduling, assessments, recruiter outreach and background checks even when no reply is required (`ALERT_ON_TYPES`); each once, interview/deadline dates within `DEADLINE_ALERT_HOURS` (72 by default, comfortably wider than the overnight gap), and, once a day, applications with no activity for 14 days.
7. Rewrites the Obsidian note for every application touched this run, plus an `_Index.md`.

The dashboard deliberately does **not** show a raw feed of recent mail. Most of what lands in a
general inbox is `not_job_related` (security alerts, receipts, personal mail), and listing it
buried the five rows that actually matter.

## Setup

### Requirements
- A Gmail account with 2-Step Verification on, and an **App Password** from [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Google dropped "less secure app access," but app passwords still work for IMAP and SMTP; that keeps this a plain password login with no OAuth app registration. Paste the 16 characters with the spaces removed.
- An Anthropic API key.
- A Telegram bot: message [@BotFather](https://t.me/botfather), send `/newbot`, copy the token into `TELEGRAM_BOT_TOKEN`. Then run `python -m jobmail.telegram_setup --write`, which validates the token, finds your chat id, writes it to `.env`, and sends a test alert. Stdlib only, so it runs anywhere preflight does.
- Python 3.11+ on the Pi.
- The jobs vault synced to a folder on the Pi (Syncthing or the Dropbox headless client — iCloud doesn't sync to Linux).

### Install on the Pi

```bash
git clone <this repo> ~/jobmail && cd ~/jobmail
sudo bash deploy/install.sh $USER          # copies to /opt/jobmail, creates venv, installs systemd units
sudo nano /opt/jobmail/.env                # fill in credentials and paths
sudo -u $USER /opt/jobmail/.venv/bin/python -m jobmail.preflight               # check IMAP, list folders
sudo -u $USER /opt/jobmail/.venv/bin/python -m jobmail.pipeline --dry-run -v   # first run, no alerts/notes
sudo systemctl start jobmail-poll.timer    # begin polling
```

The dashboard starts immediately on port 8080. Check `journalctl -u jobmail-poll -f` for polling logs.

**First run tip:** the pipeline starts from the newest UID it hasn't seen, which on a fresh database means *everything* in the inbox. If the mailbox already has history you don't want classified (each message is one Claude call), set a starting point first:

```bash
sqlite3 ~/.jobmail/jobmail.db "INSERT INTO state VALUES ('last_uid:INBOX', <uid>)"
```

### Changing the schedule

The poll cadence lives in `deploy/jobmail-poll.timer` as a systemd calendar spec. Default is `*-*-* 08..20:00`. Edit, then:

```bash
sudo cp deploy/jobmail-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart jobmail-poll.timer
systemctl list-timers jobmail-poll        # confirm the next firing
```

Two things to check when you change it. The spec uses the **system timezone**, so `timedatectl` on the Pi must show the right one. And widen `DEADLINE_ALERT_HOURS` to cover the longest gap between runs, or a deadline landing inside the gap will never be alerted.

### Private access to the dashboard

Install Tailscale on the Pi (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and, optionally, `sudo tailscale serve --bg 8080` for an HTTPS URL like `https://<pi-name>.<tailnet>.ts.net`. Only devices on your tailnet can reach it; nothing is exposed to the internet and no auth layer is needed. If you'd rather use your own subdomain later, put Caddy in front with basic auth — the app is unchanged.

### Keeping job mail out of the way of personal mail

`IMAP_FOLDER` is a Gmail label, so scoping is a filter away. In Gmail: Settings → Filters and Blocked Addresses → Create a new filter, then apply the label `Jobs` (tick "Skip the Inbox" if you want them out of the way). Set `IMAP_FOLDER=Jobs` and only labelled mail is ever classified.

Two filters cover most of it. One on `to:(you+jobs@gmail.com)` if you apply to jobs with the plus-address, and one on the ATS senders:

```
from:(greenhouse.io OR greenhouse-mail.io OR lever.co OR ashbyhq.com OR
      myworkday.com OR icims.com OR smartrecruiters.com OR jobvite.com OR
      workable.com OR breezy.hr OR taleo.net OR successfactors.com OR
      bamboohr.com OR gem.com OR dover.com OR eightfold.ai)
```

A recruiter mailing you cold still lands in the inbox and gets missed. Dragging it into the `Jobs` label fixes that: Gmail assigns a fresh IMAP UID when a message gains a label, so the next poll picks it up. That works here in a way it did not on Graph.

### Checking the mailbox connection

`python -m jobmail.preflight` needs only the four `IMAP_*` settings, so you can run it from any machine before touching the Pi. It logs in, prints every folder name, says which one it will treat as Sent, and reports how much mail is already in the inbox along with the exact SQL to skip it. Read-only: it selects folders with `readonly=True` and never fetches a body.

If the login is rejected it is almost always the password: `IMAP_PASSWORD` must be a Google App Password, not your account password, and Google displays it as four groups of four that you paste as one 16-character string. Preflight says this too.

### Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
python -m jobmail.pipeline --dry-run -v
python -m jobmail.dashboard --port 8080
```

## Configuration

All settings come from `.env` (see `.env.example`). Notable ones:

| Variable | Purpose |
|---|---|
| `IMAP_*` | Gmail address plus the App Password. `IMAP_FOLDER` is a Gmail label; `INBOX` watches everything. |
| `SMTP_*` | Only used to email *you* alerts. Defaults to the IMAP values, so the same App Password works. |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Classifier. `claude-sonnet-5-5` scored 22/22 on every eval repeat; `claude-haiku-5-5` costs ~5% as much and was as accurate except on one job scam, which it named as an employer in 2 of 3 runs. See `eval/REPORT.md` before changing it. |
| `OWNER_NAME` | Your name, so Claude knows who "you" is in the emails. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERT_EMAIL_TO` | Alert channels. Either can be blank. Never point `ALERT_EMAIL_TO` at the watched mailbox: jobmail would classify its own alerts. |
| `OBSIDIAN_VAULT_PATH`, `OBSIDIAN_SUBFOLDER` | Where notes go. Blank vault path disables Obsidian. |
| `STALE_AFTER_DAYS`, `DEADLINE_ALERT_HOURS` | Alert thresholds. Keep the deadline window wider than the longest gap between polls, or a deadline can pass unannounced. |
| `DRY_RUN` | Classify and store, but send no alerts and write no notes. |

## Metrics

`/metrics` shows response rate, median days to a reply, a funnel, applications per month, and
which ATS each came through. The funnel counts how far each application *ever got*, read from
the event log rather than the current stage, so a rejection does not erase the interviews that
preceded it.

To pull in applications from before you installed this:

```bash
python -m jobmail.backfill --list     # what it would find, spends nothing
python -m jobmail.backfill --days 180
```

It searches `[Gmail]/All Mail` (archived mail included), filters server-side on ATS senders and
application subject lines before spending anything, skips what it already has, and shows the
estimated cost before classifying. Alerts are off during a backfill.

## Obsidian notes

One note per application in `<vault>/Applications/`, named `Company - Role.md`, with YAML frontmatter (`stage`, `applied`, `last_activity`, `tags: [job-application]`) so Dataview/Bases queries work. The generated part ends at a marker comment; anything you write **below** the marker under "My notes" survives every rewrite. `_Index.md` lists all applications grouped by stage. `python -m jobmail.pipeline --resync-obsidian` regenerates every note from the database.

## Data

- `~/.jobmail/jobmail.db` — SQLite, WAL mode. Tables: `applications`, `messages`, `key_dates`, `events`, `state`. The `state` table holds `last_uid:<folder>`, `uidvalidity:<folder>`, `sent_folder` and `last_run_at`.
- `~/.jobmail/raw/<folder>/<uid>.eml` — the original message, in case you ever want to reprocess.

Handy queries:

```sql
-- what needs a reply
SELECT a.company, m.urgency, m.action_needed FROM messages m JOIN applications a ON a.id=m.application_id
WHERE m.needs_reply=1 AND m.replied_at IS NULL;

-- response rate by month applied
SELECT substr(applied_at,1,7) AS month, COUNT(*) AS applied,
       SUM(stage NOT IN ('applied')) AS got_response FROM applications GROUP BY 1;
```

## Corrections (no UI, by design)

The dashboard is read-only. To fix a misclassification or add an application that never emailed you:

```bash
sqlite3 ~/.jobmail/jobmail.db
UPDATE applications SET stage='withdrawn', closed_at=datetime('now') WHERE id=7;
UPDATE messages SET application_id=3 WHERE id=42;
INSERT INTO applications (company, company_key, role, stage, applied_at, last_activity_at, created_at, updated_at)
  VALUES ('Acme', 'acme', 'Senior PM', 'applied', datetime('now'), datetime('now'), datetime('now'), datetime('now'));
```

Then `python -m jobmail.pipeline --resync-obsidian`.

## Layout

```
jobmail/
  config.py      env → Config
  mail.py        IMAP client + MIME parsing (stdlib)
  classifier.py  Claude call with forced tool-use schema
  matcher.py     link message → application
  db.py          SQLite schema + queries
  alerts.py      Telegram + SMTP, message formatting
  obsidian.py    note + index writer
  preflight.py   IMAP connectivity check (entry point)
  telegram_setup.py  bot token check + chat id discovery + test alert (entry point)
  pipeline.py    one polling run (entry point)
  dashboard.py   FastAPI app (entry point)
  templates/     Jinja2 pages
deploy/          systemd units + install.sh
tests/           pytest (no network; stubbed classifier)
```
