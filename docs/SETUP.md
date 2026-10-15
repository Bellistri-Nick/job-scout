# Setup: your own search

How to point Job Scout and jobmail at your own search. The reviewer front page is the [README](../README.md).

## Job Scout onboarding

Setup is a conversation, and it matters more than any other step. The profile decides every score, so a bad profile fails silently. Job Scout ships as a Claude Code skill ([SKILL.md](../SKILL.md)) that interviews you and writes the profile. You never edit JSON.

The flow, in order:

1. Resume first. Drop in a resume. Claude reads it, plays it back in two sentences (level, domain, strongest scope signal), and asks what that misses. Anything the resume already answers becomes a confirmation, not a question.
2. Seven questions at most, one per message. Questions the resume answered get skipped.
3. One follow-up per vague answer. "Senior roles" becomes which titles. "Good pay" becomes a number. After one follow-up, Claude writes down a sensible default, says it did, and moves on.
4. Read-back. Claude saves your answers in your own words to `config/interview.md`, reads them back in a short paragraph, and asks "anything wrong?" That file is the record, so changing your search later means editing one answer, not redoing the interview.
5. Profile check. Claude drafts the profile, then `python run.py profile-check` explains in plain language what it will do: target titles, what never reaches you, where, pay, and how much email to expect. Every warning gets fixed before the first scan. Then one last question: "Is there a role you'd want that this would miss?"
6. Watchlist, email, dry run. You name companies you'd take a call from, and `python run.py discover` keeps only the job boards that resolve. Then you wire up email and run `python run.py scan --dry-run --open`. You review the results together before anything sends.

The questions:

| # | Claude asks | It pushes for | Becomes |
|---|---|---|---|
| 0 | "Walk me through your last two roles: title, team size, what you owned." (only without a resume) | Scope and numbers, not duties | Background, strong keywords |
| 1 | "What's the next role? The title you'd say yes to, and the one that's a step down but still okay." | Real title variants, the level floor | Target titles, seniority |
| 2 | "Where do you need to work? Home base, remote, and how many days in an office you'd accept." | Towns you'd commute to, a hard "no" | Home base, locations |
| 3 | "What's your base salary floor, and what are you aiming for? Is a posting with no salary okay?" | A number, not a range | Comp floor and target |
| 4 | "What should never reach your inbox? Roles, levels, industries, or companies." | Neighbouring functions that share vocabulary | Blocked titles, levels, companies |
| 5 | "Describe a role you'd drop everything for." | The kind of work, scope, and domain | Strong-fit signals |
| 6 | "What does a posting look like that seems right but isn't?" | The trap, in your words | Anti-signals |

A real set of answers is in [samples/scout/interview.md](../samples/scout/interview.md), and `python run.py profile-from-resume samples/scout/resume.txt --interview samples/scout/interview.md` turns it into the sample profile.

### Telegram alerts

Both agents can push to Telegram the moment something needs you: Scout when a digest lands, jobmail when a reply or a held scam needs a look. You create the bot yourself, because Claude can't:

1. Message [@BotFather](https://t.me/botfather) on Telegram, send `/newbot`, and copy the token.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN`, then send your new bot any message.
3. Run `python run.py telegram-setup` for Scout, or `python -m jobmail.telegram_setup --write` for jobmail. Scout's command finds your chat id, prints it for you to put in `.env`, and sends a test. jobmail's `--write` saves the chat id for you, then sends a test.

With the token unset, Telegram is a silent no-op, and a failed push never breaks a scan. Email still goes out.

### jobmail setup

jobmail has no interview. It learns your search from your mail. You give it a Gmail account with an App Password, an Anthropic API key, and an `OWNER_NAME` so the classifier knows who "you" is. Then `python -m jobmail.preflight` checks the connection and `python -m jobmail.pipeline --dry-run -v` does a first run with no alerts. To recover applications you sent before installing, `python -m jobmail.backfill --list` shows what it would find and spends nothing. The full steps are in [jobmail/docs/OPERATIONS.md](../jobmail/docs/OPERATIONS.md).
