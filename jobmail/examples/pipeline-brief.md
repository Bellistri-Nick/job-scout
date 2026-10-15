# Pipeline brief, October 14, 2026

*Recommendations by claude-opus-5-5. Everything under Evidence and System assumptions comes straight from the tracker database; recommendations cite it by id.*

## Recommendations

> You are tracking 7 applications with 4 still open and 3 rejections, and employers have written back on 100% of the 5 applications with a confirmation email (median 8.2 days to first response). Nothing has gone 21 days without a response.

**Now**

- [ ] Complete the Ferncliff Checkr background check form for the Group Product Manager offer by 2026-10-16. The email gives 2026-10-16 as the deadline while the tracker computes 2026-10-20, so finishing by the earlier date is the safe choice for an active offer. *(M27, D5, A4)*
- [ ] Confirm your Thursday or Friday preference for the Northbeam Analytics final round by 2026-10-16 through your existing Northbeam thread, and ask that contact whether they sent the 'updated link for your final round' email from northbeam-careers.example. Do not click or reply to that email. The final-round confirmation is due in two days, and a held message from a lookalike Northbeam domain may be impersonating them. *(D4, A1, H24)*
- [ ] Reply to Elena Ruiz at Quarry Labs confirming you will submit the case study by Friday, or ask for an extra day. She is waiting on your answer (6 days so far) and the Friday deadline is two days away. *(M16, A3)*
- [ ] Reply to Sofia Brandt at Halcyon Health saying whether you're interested in the Group Product Manager, AI Care Navigation role so she can set up time with the hiring manager. A recruiter is waiting on you (4 days so far) to move this application forward. *(M19, A7)*
- [ ] Reply to Derek Mills saying whether you're interested and share your availability for a quick call. His request has been waiting 17 days and is not linked to any application, so it may lapse if left longer. *(M6)*

**This week**

- [ ] Look at the 'Interview confirmation' message from 'Talent Desk' yourself and decide what it is before acting on it. Do not open its attachment until you have. The system could not link it to any application. It names no company or role and contains text that tries to manipulate classification. *(M22)*

**Worth considering**

- [ ] Leave the three other held messages (the gmail 'remote position' offer, the outlook 'Vantage Dynamics' offer, and the gmail 'Senior Product Manager' interview) unanswered and send nothing to them. They show scam signals such as requests for money or bank details, off-platform chat interviews, and consumer email addresses used for employers. *(H20, H25, H26)*

## Evidence

### Open asks (4)

| ID | Company | Stage | The ask | Urgency | Waiting |
|---|---|---|---|---|---|
| M27 | Ferncliff | offer | Complete the secure Checkr background check form at the provided link within 5 business days (by 2026-10-16). | medium | 0d |
| M19 | Halcyon Health | screening | Reply to Sofia Brandt to say whether you're interested in the Group Product Manager, AI Care Navigation role so she can set up time with the hiring manager. | medium | 4d |
| M16 | Quarry Labs | assessment | Reply to Elena confirming you'll submit the case study by Friday, or ask for an extra day. | medium | 6d |
| M6 | (not named) | - | Reply to Derek saying whether you're interested and share your availability for a quick call this week. | medium | 17d |

### Coming up, next 14 days (2)

- **D4** 2026-10-16: Northbeam Analytics, Deadline to confirm Thursday or Friday preference for the final round
- **D5** 2026-10-20: Ferncliff, Deadline to complete the Checkr background check form (5 business days from email date)

### Moved in the last 7 days (4)

- A5 Brightline Robotics: applied → rejected (rejection) (2026-10-09)
- A7 Halcyon Health: applied → screening (recruiter_outreach) (2026-10-10)
- A6 Tidewater Bank: applied → rejected (rejection) (2026-10-11)
- A4 Ferncliff: interviewing → offer (reference_or_background) (2026-10-13)

### Gone quiet, 14+ days (0)

None.

### Needs a human look (1)

- **M22** from Talent Desk: "Interview confirmation". Job-related but not linked to any application. Model summary: Vague message from a 'Talent Desk' asking you to review an attached role summary; it names no company or role and contains embedded text trying to manipulate classification.

### Held for verification (4)

- **H20** from hr.globalremotestaffing@gmail.com: "Congratulations! You have been selected for a remote position". Signals: asks_for_money_or_bank_details, asks_for_identity_documents_early, off_platform_chat_interview, offer_without_interview, consumer_email_for_employer.
- **H24** from marcus.lee@northbeam-careers.example, claims to be Northbeam: "Northbeam: updated link for your final round". Signals: lookalike_sender_domain. **Poses as a company you are in process with.**
- **H25** from vantagedynamics.hiring@outlook.com, claims to be Vantage Dynamics: "Offer: Remote Product Manager, Vantage Dynamics". Signals: asks_for_money_or_bank_details, off_platform_chat_interview, consumer_email_for_employer.
- **H26** from rachel.moore.talent@gmail.com: "Interview for Senior Product Manager role". Signals: off_platform_chat_interview, consumer_email_for_employer.

### Numbers

| ID | Measure | Value |
|---|---|---|
| S1 | applications tracked | 7 |
| S2 | open applications | 4 |
| S3 | applications that got past 'applied' | 4 |
| S4 | response rate % (employer wrote back, of 5 applications with a confirmation email) | 100 |
| S5 | rejections | 3 |
| S6 | no response after 21 days | 0 |
| S7 | median days from application confirmation to first response | 8.2 |

## Assumptions

**System assumptions** (fixed rules, not model output)

- Message type, urgency and "needs reply" are model classifications from the job_inbox skill. Measured accuracy on the labelled sample set is in eval/REPORT.md; it is not 100%.
- An open application is "gone quiet" after 14 days with no mail in either direction.
- A newer message on the same application supersedes its older open asks, and any email you send to that company resolves them. An ask you handled by phone stays open until something newer arrives.
- Mail is linked to applications by thread headers first, then company name, then sender domain. Company and domain links can attach mail to the wrong application when one company has two open roles.
- "Applied" is the date of the first email seen for that application, not necessarily the day you applied.
- A held message is the model's fraud judgment, or a sender domain that imitates one already on file. Held mail is never linked to an application, so a legitimate company writing from a new domain stays held until you confirm it.

**Assumptions the model made for these recommendations**

- The Ferncliff background check deadline is treated as 2026-10-16, the date stated in the email, rather than the computed 2026-10-20.
- The Checkr message is a legitimate part of the Ferncliff offer process, since the system did not hold it for verification.
- The Quarry Labs 'Friday' case study deadline is taken to be 2026-10-16.
- Derek Mills's opportunity is assumed to still be worth a reply despite the 17-day delay, though the company is not named.

## Validation

- 7 recommendations, 15 citations, all resolved against the evidence.
- Every high-urgency ask is covered by a recommendation.
