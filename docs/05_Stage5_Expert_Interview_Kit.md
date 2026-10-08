# Stage 5: Industry Expert Interview & Validation Kit

**Order of work:** (1) shortlist experts → (2) faculty approval of the profile → (3) schedule *after* the PoC runs with
real models → (4) interview (record video) → (5) full transcript → (6) document feedback and the changes made.
If the expert changes, fresh approval is needed.

## 5.1 Who to target (marks depend on the approved level)
| Level (marks) | Example titles that fit this problem |
|---|---|
| **Director (5)** | Head / VP / AVP Fraud Risk Management; Head of Fraud Control Unit; Director, Financial Crime / Fraud Analytics; Chief Risk Officer of a payments company or NBFC; Head of Digital Banking Risk |
| Manager (3) | Fraud Risk Manager; Senior Manager, Transaction Monitoring; FRM Operations Manager |
| Analyst / Specialist (2) | Senior Fraud Analyst; Fraud Investigation Specialist; AML Analyst |

**Domain relevance matters:** fraud risk, transaction monitoring, financial-crime investigation or AML at a bank, NBFC,
card network, payment aggregator or FRM vendor. Avoid unrelated roles (general IT, marketing).

**Where to find them:** faculty and alumni networks; LinkedIn search (`"fraud risk" head bank India`,
`"fraud control unit" VP`); speakers at fraud and financial-crime conferences; FRM vendors' customer-success leads.

### Outreach message (LinkedIn / e-mail)
> Dear [Name], I'm a [programme] student at [institute]. Our team has built a working prototype that uses AI agents
> to investigate bank fraud alerts, with human approval for every adverse action. We would value 45 minutes of your
> view on whether the process and controls are realistic. We'll show a short live demo and ask five questions about
> how fraud investigation works in practice. The interview is recorded for academic assessment only, and we will not
> share your contact details. Would any slot next week work? Thank you, [Name, team, phone].

## 5.2 Faculty approval request (submit before scheduling)
| Field | Entry |
|---|---|
| Expert name | |
| Current designation | |
| Organisation | |
| Function / domain | e.g. Fraud Risk Management, Retail Banking |
| Relevant experience | years in fraud / risk; key responsibilities |
| Professional profile link | LinkedIn URL |
| Proposed level | Director / Manager / Analyst |
| Justification (2–3 lines) | Why this person can validate fraud-investigation processes and the PoC |
| Proposed interview date & mode | |

**E-mail to faculty:** "Dear Prof. [Name], please find our proposed industry expert for the Banking Fraud Investigation
project: [table above]. We request approval of the profile and designation level. Evidence of designation (LinkedIn
profile / company page) is attached. Regards, Team [x]."

*Privacy:* keep the expert's personal phone and e-mail out of all submitted documents and the website. The name,
designation and organisation are enough, if the expert consents.

## 5.3 Interview plan (45–60 minutes, recorded)
| Time | Segment | Who leads |
|---|---|---|
| 0–3 | Introductions, consent to record and publish the transcript for assessment | Member 1 |
| 3–28 | **Five mandatory questions** (5.4) | Members 1–2 |
| 28–43 | Walk-through: AS-IS → TO-BE (A/H/E) → architecture → **live PoC** (ALT-1001 auto-close, ALT-3001 escalation with L1 blocked / L2 approves, ALT-3004 injection) → guardrails mapping | Members 3–4 |
| 43–55 | Validation questions (5.5) | Member 5 |
| 55–60 | Thanks, permission to quote, next steps | Member 1 |

Recording: Teams / Zoom / Google Meet with cameras on. Turn on auto-transcription, then **correct the transcript manually
against the video**.

## 5.4 Five mandatory questions, with follow-up probes
1. **What makes fraud investigation a complex task in your organisation? What information, rules, evidence and judgement must an investigator normally consider?**
   *Probes:* Which signals matter most for account takeover vs scams vs mules? Where does judgement override rules? How are decisions documented today?
2. **Which people, systems, data sources or departments does a fraud investigator depend on, and where do these dependencies create difficulties or delays?**
   *Probes:* How many systems does an analyst open per alert? Contact centre and branch hand-offs? Telco SIM-swap data, NPCI, other banks for mule accounts?
3. **How important is speed in fraud investigation, and which stages of the process are most time-sensitive?**
   *Probes:* Typical time from alert to decision? SLAs today? The "golden hour" for recovery via 1930? Night and weekend coverage?
4. **What types and volume of information must investigators examine, and what problems arise when information is incomplete or spread across different systems?**
   *Probes:* Alerts per analyst per day? Share of false positives? What happens when a system is down or data is missing?
5. **What kinds of unusual or ambiguous fraud cases are difficult to handle using standard rules, and how are such cases currently managed?**
   *Probes:* Authorised-push-payment / "digital arrest" scams where the genuine customer pays? Frequent travellers? Business accounts? Who decides?

## 5.5 Validation questions (feasibility · risks · usefulness · improvements)
- **Feasibility:** Could this run in your environment? What would block it: data access, model hosting, IT security, vendor policy?
- **Usefulness:** Which part saves the most time: evidence pack, agent summaries, routing? Would analysts trust the recommendation?
- **Thresholds:** Is auto-closing only "PROCEED, risk < 30, confidence ≥ 0.8, no flags" with 10% QA sampling acceptable? What would you change?
- **Risks:** What worries you most: hallucination, automation bias, prompt injection, data privacy, regulatory acceptance?
- **Controls:** Are the A/H/E boundaries right? Should any action move between autonomous and human?
- **Regulation:** How do RBI expectations (e.g. the FREE-AI framework, customer-liability rules, STR obligations) affect deploying this?
- **Improvements:** What one feature would make this production-worthy?
- **Rating:** On 1–5, how feasible / useful / safe is the design?

## 5.6 Transcript template (`docs/expert/Interview_Transcript.docx` or `.md`)
```
Project: Governed Agentic AI for Banking Fraud Investigation · Team [x]
Expert: [Name], [Designation], [Organisation]  (approved by faculty on [date], level: [Director/Manager/Analyst])
Date / mode / duration: [ ] · Interviewers: [names] · Recording file: [link]

[00:00] Interviewer (Name): ...
[00:45] Expert: ...
Q1 [mm:ss] Interviewer: What makes fraud investigation a complex task ...
     Expert: ...
...
```

## 5.7 Feedback capture and response
| # | Expert comment (quote + timestamp) | Category (feasibility / risk / usefulness / improvement) | Our response / change made | Status |
|---|---|---|---|---|
| 1 | | | | |

Include a short **"What the expert validated / challenged / changed"** summary in the report and on the website.
If the expert suggests a threshold change, it is a one-line `.env` change. Re-run the benchmark and show the before and after.
