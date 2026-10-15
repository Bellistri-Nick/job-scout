# Process map

Two maps: how a job search runs today, and how it runs with Job Scout and jobmail. Orange boxes are model steps. Gray boxes are plain code. Blue boxes are you.

## Before: the manual search

Ten steps, and only two of them need judgment (3 and 7). The rest is fetching and bookkeeping, and that is where the search falls behind.

```mermaid
flowchart LR
    S1["1. Check career pages and boards<br/>40+ pages, weekly at best"] --> S2["2. Read each posting<br/>hours a week on wrong-fit roles"]
    S2 --> S3{"3. Worth applying to?<br/>keywords lie"}
    S3 -->|yes| S4["4. Apply"]
    S3 -->|no| S1
    S4 --> S5["5. Log it in a spreadsheet<br/>skipped on busy days"]
    S5 --> S6["6. Scan the inbox<br/>alerts drown human mail"]
    S6 --> S7{"7. Needs me, and by when?<br/>deadlines buried in prose"}
    S7 -->|yes| S8["8. Reply, book, submit"]
    S7 -->|missed| X["Deadline slips,<br/>or a scam gets a reply"]
    S8 --> S9["9. Update the stage<br/>rarely happened"]
    S9 --> S10["10. Review follow-ups<br/>never happened"]

    classDef judge fill:#fde7c8,stroke:#c26a00,color:#222
    classDef human fill:#dce9fb,stroke:#2b5fb4,color:#222
    classDef bad fill:#fbdcdc,stroke:#b42b2b,color:#222
    class S3,S7 judge
    class S1,S2,S4,S5,S6,S8,S9,S10 human
    class X bad
```

## After: two agents, one human

Job Scout finds roles. jobmail tracks what happens after you apply. A read-only bridge lets Scout skip roles you already applied to. The model makes two judgment calls: re-rank postings, and read and classify email. It also drafts the weekly brief's recommendations, and code verifies every citation. Everything else is code that tests can pin down.

```mermaid
flowchart TB
    subgraph SCOUT["Job Scout (runs weekday mornings)"]
        direction LR
        A1["Fetch public job feeds<br/>Greenhouse, Lever, Ashby,<br/>Workable, SmartRecruiters"] --> A2["Dedupe, then rules score<br/>comp floor, titles, location,<br/>blocklist"]
        A2 --> A3["Model re-rank<br/>fit against your profile"]
        A3 --> A4["Ranked digest<br/>emailed to you"]
    end

    subgraph YOU["You"]
        direction LR
        H1["Read digest,<br/>decide, apply"]
        H2["Reply, book,<br/>submit"]
        H3["Confirm or dismiss<br/>held messages"]
        H4["Work through the<br/>brief's checkboxes"]
    end

    subgraph MAIL["jobmail (polls the inbox)"]
        direction LR
        B1["Read new mail<br/>(read-only)"] --> B2["Model classifies<br/>company, role, type,<br/>needs reply, deadline"]
        B2 --> B3{"Sender domain matches<br/>a known application?"}
        B3 -->|yes| B4["Link to application,<br/>move stage forward only"]
        B3 -->|lookalike| B5["Hold. Never link,<br/>never learn the domain"]
        B4 --> B6[("SQLite memory<br/>applications, messages,<br/>key dates, event log")]
        B5 --> B6
        B6 --> B7["Alert on Telegram or email:<br/>needs a reply, deadline in 72h,<br/>possible scam"]
        B6 --> B8["Weekly brief<br/>code writes evidence,<br/>model recommends,<br/>code verifies every citation"]
    end

    A4 --> H1
    H1 -->|applied| B1
    B6 -.->|bridge: already applied| A2
    B7 --> H2
    B5 --> H3
    B8 --> H4

    classDef model fill:#fde7c8,stroke:#c26a00,color:#222
    classDef code fill:#ececec,stroke:#666,color:#222
    classDef human fill:#dce9fb,stroke:#2b5fb4,color:#222
    classDef hold fill:#fbdcdc,stroke:#b42b2b,color:#222
    class A3,B2 model
    class A1,A2,A4,B1,B3,B4,B6,B7,B8 code
    class H1,H2,H3,H4 human
    class B5 hold
```

### Where a human decides

| Point | What the human does |
|---|---|
| Profile setup | Reads and approves the drafted profile before the first scan |
| Digest | Decides which roles to apply to. Scout never applies for you |
| Reply or deadline alert | Writes the reply. jobmail never replies, drafts, or deletes |
| Held message | Confirms or dismisses a possible scam. Held mail is never acted on |
| Classifier failure | A failed model call lands on the "needs a human look" list at high urgency, not filed as handled |
| Weekly brief | Acts on, or ignores, each recommendation. Code checks that every one cites real evidence |

### Where the model sits, and where it doesn't

| Step | Model or code | Why |
|---|---|---|
| Rank a posting against your profile | Model, after code rules | Scope and level live in the prose, and keywords lie |
| Read and classify an email | Model | Free text in, typed record out |
| Link mail to an application | Code | Thread headers, then company, then sender domain. Explainable and testable |
| Move a stage | Code | A forward-only state machine. A model should not decide an offer un-happens |
| Catch a lookalike sender | Code, reading memory | The model passed the lookalike as genuine in 6 of 6 eval runs. Only the history shows the tell |
| Write the brief's recommendations | Model, then code | Each one must cite an evidence id that code verifies |
