# Pipeline brief, October 10, 2026

*Recommendations by claude-opus-5-5. Everything under Evidence and System assumptions comes straight from the tracker database; recommendations cite it by id.*

## Recommendations

> Of 7 tracked applications, 4 are still open and 4 got past 'applied', a 57% response rate with a median of 4 days to first response. There have been 3 rejections and none left without a response after 21 days.

**Now**

- [ ] Reply to Northbeam Analytics through your existing, known thread to confirm Thursday or Friday for the final round before the 2026-10-12 deadline, and in the same reply ask that contact whether the 'updated link for your final round' email from marcus.lee@northbeam-careers.example is genuine, without clicking anything in that email. The final-round preference is due in two days, and a lookalike-domain message posing as Northbeam is targeting this same round. *(D4, A1, H24)*
- [ ] Reply to Elena Ruiz at Quarry Labs about the Developer Platform case study: confirm it is submitted, or ask for the extra day she offered. Elena asked for a confirmation five days ago, and the Friday she mentioned may already have passed. *(M16, A3)*
- [ ] Reply to Sofia Brandt at Halcyon Health saying whether you are interested in the Group Product Manager, AI Care Navigation role so she can schedule time with the hiring manager. This is a live recruiter request that is three days old and is the next step to move this screening forward. *(M19, A7)*
- [ ] Reply to Derek Mills with either your interest and availability for a quick call, or a short decline. His request has been waiting 16 days, and he asked about a call this week. *(M6)*

**This week**

- [ ] Complete the Ferncliff Checkr background check form before 2026-10-16. Ferncliff moved to offer, and this check has a firm deadline of five business days. *(M27, D5, A4)*
- [ ] Have a human look at the 'Interview confirmation' message from 'Talent Desk' before acting on it or opening its attachment. The system could not link it to any application, and it names no company or role. *(M22)*

**Worth considering**

- [ ] Leave the held messages from hr.globalremotestaffing@gmail.com, vantagedynamics.hiring@outlook.com and rachel.moore.talent@gmail.com unanswered, and do not click or send anything to them. They show suspected-scam signals such as requests for money or bank details, off-platform chat interviews, and consumer email addresses. *(H20, H25, H26)*

## Evidence

### Open asks (4)

| ID | Company | Stage | The ask | Urgency | Waiting |
|---|---|---|---|---|---|
| M27 | Ferncliff | offer | Complete the secure Checkr background check form at the provided link within 5 business days (by 2026-10-16). | medium | 0d |
| M19 | Halcyon Health | screening | Reply to Sofia Brandt to say whether you're interested in the Group Product Manager, AI Care Navigation role so she can set up time with the hiring manager. | medium | 3d |
| M16 | Quarry Labs | assessment | Reply to Elena confirming you'll submit the case study by Friday, or ask for an extra day. | medium | 5d |
| M6 | (not named) | - | Reply to Derek saying whether you're interested and share your availability for a quick call this week. | medium | 16d |

### Coming up, next 14 days (2)

- **D4** 2026-10-12: Northbeam Analytics, Deadline to confirm Thursday or Friday preference for the final round
- **D5** 2026-10-16: Ferncliff, Deadline to complete the Checkr background check form (5 business days from email date)

### Moved in the last 7 days (4)

- A5 Brightline Robotics: applied → rejected (rejection) (2026-10-05)
- A7 Halcyon Health: applied → screening (recruiter_outreach) (2026-10-06)
- A6 Tidewater Bank: applied → rejected (rejection) (2026-10-07)
- A4 Ferncliff: interviewing → offer (reference_or_background) (2026-10-09)

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
- A held message is the model's fraud judgment, or a sender domain that imitates one already on file. Held mail is never linked to an application, so a legitimate company writing from a new domain stays held until you confirm it.

**Assumptions the model made for these recommendations**

- The Friday in Elena's request was 2026-10-09, so the case study may already be due or late.
- Your existing Northbeam thread is with a genuine Northbeam contact and is a safe channel for verifying the held message.
- Derek Mills's message is a genuine job lead worth answering, even though no company or role is named.

## Validation

- 7 recommendations, 15 citations, all resolved against the evidence.
- Every high-urgency ask is covered by a recommendation.
