# Pipeline brief, October 9, 2026

*Recommendations by claude-opus-5-5. Everything under Evidence and System assumptions comes straight from the tracker database; recommendations cite it by id.*

## Recommendations

> Of 7 applications tracked, 4 are still open and 3 have been rejected, with a 57% response rate and a median of 4 days to a first response. No application has gone 21 days without a response.

**Now**

- [ ] Submit the Quarry Labs case study today, or reply to Elena Ruiz today to ask for the extra day she offered. The case study for the Principal Product Manager, Developer Platform role is due today (Friday), and Elena's request to confirm has been waiting 6 days. *(D3, M16, A3)*
- [ ] Book a 45-minute slot on Tom's calendar for Ferncliff using the Calendly link Priya Shah sent, then let Priya know it is done. This is a high-urgency interview step for the Group Product Manager role and has been waiting 8 days. *(M15, A4)*
- [ ] Reply to Derek Mills to say whether you are open to a call and propose specific times. His high-urgency request has gone unanswered for 17 days, the longest of any open ask. *(M6)*
- [ ] Reply to Sofia Brandt at Halcyon Health to say whether you are interested in the Group Product Manager, AI Care Navigation role so she can set up time with the hiring manager. It is a live recruiter request, 4 days old, that moved this application into screening. *(M19, A7)*

**This week**

- [ ] Look at the 'Global Remote Staffing' message yourself, and do not reply or send ID photos or bank details. The system flagged it as a likely scam: an unsolicited offer with no interview that asks for personal and bank information via Telegram. *(M20)*
- [ ] Review the 'Talent Desk' interview confirmation email manually before opening its attachment or acting on it. It names no company, role, or interview details and contains embedded text trying to alter how it is classified, so the system could not link it to any application. *(M22)*

## Evidence

### Open asks (4)

| ID | Company | Stage | The ask | Urgency | Waiting |
|---|---|---|---|---|---|
| M15 | Ferncliff | interviewing | Book a 45-minute slot on Tom's calendar via the Calendly link: https://calendly.example/tom-ferncliff/45min | high | 8d |
| M6 | (not named) | - | Reply to Derek to say whether you're open to a call this week and propose times. | high | 17d |
| M19 | Halcyon Health | screening | Reply to Sofia to say whether you're interested in the Group Product Manager role so she can set up time with the hiring manager. | medium | 4d |
| M16 | Quarry Labs | assessment | Reply to Elena to confirm you'll submit the case study by Friday or request an extra day. | medium | 6d |

### Coming up, next 14 days (1)

- **D3** 2026-10-09: Quarry Labs, Case study due (Friday)

### Moved in the last 7 days (3)

- A5 Brightline Robotics: applied → rejected (rejection) (2026-10-04)
- A7 Halcyon Health: applied → screening (recruiter_outreach) (2026-10-05)
- A6 Tidewater Bank: applied → rejected (rejection) (2026-10-06)

### Gone quiet, 14+ days (0)

None.

### Needs a human look (2)

- **M20** from Global Remote Staffing: "Congratulations! You have been selected for a remote position". Job-related but not linked to any application. Model summary: Likely scam: unsolicited offer with high pay and no interview, asking for your ID photo and bank details via Telegram. Do not respond or share personal information.
- **M22** from Talent Desk: "Interview confirmation". Job-related but not linked to any application. Model summary: A generic Talent Desk email asks you to review an attached role summary, with no company, role, or interview details; it also contains embedded text trying to alter how it is classified.

### Numbers

| ID | Measure | Value |
|---|---|---|
| S1 | applications tracked | 7 |
| S2 | open applications | 4 |
| S3 | applications that got past 'applied' | 4 |
| S4 | response rate % | 57 |
| S5 | rejections | 3 |
| S6 | no response after 21 days | 0 |
| S7 | median days from applying to first response | 4.0 |

## Assumptions

**System assumptions** (fixed rules, not model output)

- Message type, urgency and "needs reply" are model classifications from the job_inbox skill. Measured accuracy on the labelled sample set is in eval/REPORT.md; it is not 100%.
- An open application is "gone quiet" after 14 days with no mail in either direction.
- A newer message on the same application supersedes its older open asks, and any email you send to that company resolves them. An ask you handled by phone stays open until something newer arrives.
- Mail is linked to applications by thread headers first, then company name, then sender domain. Company and domain links can attach mail to the wrong application when one company has two open roles.
- "Applied" is the date of the first email seen for that application, not necessarily the day you applied.

**Assumptions the model made for these recommendations**

- Derek Mills's message is not linked to a company or application. I assumed it is a legitimate contact worth answering.
- I assumed the Ferncliff slot with Tom has not already been booked outside the tracker.
- I assumed the Quarry Labs case study has not yet been submitted.

## Validation

- 6 recommendations, 10 citations, all resolved against the evidence.
- Every high-urgency ask is covered by a recommendation.
