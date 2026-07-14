# ICP Interview Kit — Days 1–30 Problem and Buyer Validation

Internal document. "Lethe" is an internal codename. Per SPEC §11.2, select a
customer-facing working name before the first interview and never use "Lethe"
externally — not in outreach, calendar invites, slides, or conversation.

This kit operationalizes SPEC §11.2 (days 1–30) and §11.5 (kill and narrow
criteria). Every threshold below is taken from those sections; do not adjust
numbers without changing the SPEC first.

---

## 1. Goal and method

**Goal.** Test hypotheses 1, 2, 5, and 7 from SPEC §11.1 before any solution
validation:

1. Costly cross-system lifecycle incidents occur often enough to fund a product.
2. Head of AI Platform owns urgency and budget, not only technical interest.
5. Scoped receipts reduce evidence-preparation work.
7. Buyers value coordination beyond source-platform and store-native controls.

**Method.** Interview 15 ICP participants across at least 10 companies within
30 days. These are incident-collection interviews, not pitches. We are hunting
for one specific artifact per interview: a concrete, dated case where a single
authenticated source lifecycle event (delete, correction, expiry, or permission
revocation) failed to propagate across at least two derivative systems outside
the source platform's control. Everything else — quantification, workaround,
budget mapping — hangs off that incident.

**Rules of engagement.**

- No pitch. Do not describe the product until the interview is scored. If asked
  "so what do you do," say: "We're researching how teams handle knowledge
  lifecycle across RAG and agent systems. I'd rather hear your experience first;
  happy to share what we're exploring at the end."
- Ask about the past, never the hypothetical future. "Tell me about the last
  time X happened" beats "would you want a tool that does X" every time.
  "Would you buy" is banned.
- People, not surveys. 45 minutes, one participant, video or in person. Record
  with consent or take verbatim notes.
- Two interviewers where possible: one asks, one logs against the capture
  fields in section 4.
- Score within 24 hours of the interview while detail is fresh.
- Max two participants per company count toward the 15; companies count once
  toward the 10-company floor.

**What we must record per interview (SPEC §11.2):** budget owner, deployment
owner, mutation approver, security objections, and current workaround. An
interview missing these fields is incomplete, not merely weak.

---

## 2. Screener criteria

Screen before booking. A 45-minute slot spent on a poor-fit participant is a
lost slot against a 30-day clock.

### Qualifies as ICP (SPEC §10.2 strong fit)

The participant's organization should meet most of the following, and the
participant must be close enough to operations to describe real incidents:

- Centralized AI platform team (or the person building toward one).
- At least three RAG or agent applications in production or late pilot.
- At least two derivative stores or memory/cache systems (vector DBs,
  retrieval caches, summary stores, agent memory).
- Frequent content, identity, or policy changes in the source corpus.
- Sensitive, licensed, regulated, or customer-controlled data flowing into
  those applications.
- Repeated manual lifecycle cleanup or audit-evidence burden (they may not use
  those words; probe for "we had to go clean up the index by hand").
- Organizationally able to deploy a customer-side agent (matters for later
  stages; note it, don't gate on it).

**Participant roles, in priority order:** Head of AI Platform (or closest
equivalent: Director of AI/ML Platform, VP AI Engineering), then platform/ML
infrastructure engineers who operate the stack, then security/privacy/data
governance stakeholders who consume evidence. Target mix across the 15: at
least 8 buyer-level (budget-owning or budget-adjacent), the rest operators and
evidence consumers. We need buyer-level coverage because the buyer thesis
(§11.5) is scored on budget ownership, and we need at least 3 budget owners to
even test the price band.

### Disqualifiers (SPEC §10.2 poor fit)

- One application and one store.
- Reliable source identifiers on every vector, with native deletion and
  permission sync already meeting their requirements.
- Negligible lifecycle-event volume (no meaningful deletes, corrections,
  expiries, or permission changes).
- Unwilling to grant even read-only local scanning access (disqualifies for
  pipeline, not necessarily for the interview itself — a "no read access ever"
  posture is a data point).
- Requirements centered on model-weight unlearning, forensic backup erasure,
  or universal deletion proof — out of scope for v0.1 and a mismatch signal.
- Vendor employees, consultants selling into the same space, and anyone who
  cannot speak to a specific deployment (analysts, pure advisors).

Borderline case worth keeping: a team fully standardized on one managed
platform (Glean, Bedrock Knowledge Bases) with no derivatives outside it. They
are "enough when" cases per SPEC §10.7 — interview one or two deliberately to
test hypothesis 7, but flag them as substitute-covered in scoring.

---

## 3. Interview script (~45 minutes)

Times are guides. If the participant is mid-incident-story at minute 20, let
them run; the incident is the payload.

### 3.1 Opening framing (2 min — no pitch)

> "Thanks for the time. We're doing research on how teams operate AI knowledge
> systems — specifically what happens to search indexes, copilots, and agents
> when the underlying documents change: things get deleted, corrected, expire,
> or someone loses access. I'm not selling anything today and there's no demo.
> I want to hear how this actually works at [company], especially the messy
> parts. Nothing you say will be attributed outside my team. Okay to start?"

Do not mention receipts, propagation, gates, revocation, or any product
concept. If they ask what you're building, defer to the end.

### 3.2 Context mapping (5 min)

Establish the topology so incident stories have coordinates.

- "Walk me through your AI applications that sit on top of internal or customer
  content — what do you have running today?"
- "For [named app], where does the content come from, and what does it pass
  through before a user sees an answer?" (Listen for: chunking pipelines,
  embedding stores, caches, summary layers, agent memory. Note each store by
  name.)
- "Who owns that pipeline day to day?"
- "How often does the source content change — deletions, corrections, access
  changes?"

### 3.3 Incident elicitation (13 min — the core)

Move from open to specific. Take each lifecycle event type in turn if the open
questions don't surface stories.

Open:

- "Tell me about the last time content that should have been gone — deleted,
  corrected, expired, or restricted — showed up in an AI answer, a copilot
  response, or an agent's output."
- "What's the worst version of that you've seen here?"

If nothing surfaces, probe each event type with past-tense prompts:

- Deletion: "Has anyone ever deleted a document and later found the AI still
  answering from it? Walk me through that."
- Correction: "When a document gets corrected — wrong price, wrong policy,
  wrong legal language — what happened downstream the last time that occurred?"
- Expiry: "Do you have content with a shelf life — contracts, certifications,
  policies? Tell me about a time expired content kept circulating."
- Permission revocation: "Has someone lost access to a source system but kept
  effectively seeing its content through search or a copilot? When?"

For each incident, drill until you can write a one-paragraph case:

- "When was this, roughly?"
- "How was it discovered — who noticed, and how long after the source change?"
- "Which systems still had the content?" (Count them. The broad-problem
  threshold requires the failure to span **at least two derivative systems
  outside source control** — a single vector store misconfiguration does not
  qualify.)
- "Then what happened? Who got pulled in?"
- "How did you confirm it was actually gone? Did it ever come back?" (Listen
  for re-ingestion and backup-restore resurrection.)

Do not suggest failure modes they haven't raised. If they describe only a
single-store cleanup, note it honestly — it scores as non-qualifying.

### 3.4 Quantification (10 min)

Anchor every number to the incidents just described, then generalize. These
map directly to the ROI discovery inputs in SPEC §10.6.

- Frequency: "How many times has something like this happened in the last 12
  months? Which ones do you remember?" (events/year)
- Blast radius: "In that incident, how many systems had to be touched?"
  (systems/event)
- People: "Who worked on the cleanup — how many people, from which teams?"
  (people/event; note whether legal, security, or customer-facing teams were
  pulled in)
- Time: "From discovery to 'we're confident it's handled,' how long did that
  take? How many actual working hours across everyone involved?" (hours/event;
  time-to-correct)
- Consequences: "What did it cost you beyond the cleanup — customer
  escalation, regulator or auditor attention, a deal, an internal incident
  review?" (Record concrete consequences only; do not lead with "compliance
  risk.")
- Audit load: "How many audits or security reviews per year ask you to prove
  content handling in these systems? How many hours does preparing that
  evidence take each time?" (audit cycles/year, evidence hours/cycle)
- If they volunteer rates or budgets, capture loaded labor rate; do not push.

An interview counts toward the quantification threshold when the participant
puts a credible number on **people, systems, time, or audit cost** — one solid
dimension is enough (SPEC §11.2), but capture as many as they'll give.

### 3.5 Current workarounds (6 min)

- "Today, when a source document is deleted or corrected, what actually
  happens downstream — walk me through the mechanics."
- "What have you built or bought for this?" (Listen for: full re-index cycles,
  cron jobs, runbooks, tickets to app teams, platform-native deletion policies,
  Glean/BigID/OneTrust/Collibra deployments, Bedrock KB deletion policy,
  Pinecone/Chroma ID deletes, Mem0-style memory APIs, pure internal
  orchestration — the substitute landscape in SPEC §10.7.)
- "Where does that approach break down? When did it last break down?"
- "What does it cost to run — whose time, how often?"
- "Have you tried to fix this properly? What happened to that effort?"
  (An abandoned internal project is a strong pain signal; a satisfied "our
  weekly re-index is fine" is a strong substitute-adequacy signal. Record
  either honestly.)

### 3.6 Evidence and audit (5 min)

Tests hypothesis 5 without describing receipts.

- "When someone — auditor, security reviewer, customer, DPO — asks you to
  prove that deleted or restricted content is really out of your AI systems,
  what do you hand them?"
- "Tell me about the last time you had to produce that. How long did it take
  and who assembled it?"
- "Was what you produced accepted, or did it turn into a longer argument?"
- "What can you *not* currently prove that you've been asked to prove?"

### 3.7 Buyer mapping and closing probe (4 min)

Buyer mapping (required capture fields):

- "If your team decided to spend real money fixing this, whose budget would it
  come from?" (budget owner)
- "Who would have to run and operate whatever you deployed?" (deployment owner)
- "If a tool needed to modify or delete data in your stores, who has to
  approve that?" (mutation approver)
- "What would your security team say about a third-party agent with read
  access to these systems?" (security objections)
- "Where does fixing this sit on your roadmap for the next two quarters —
  funded, wishlist, or not on it?" (urgency; this feeds the buyer-thesis
  score)

Closing probe — now, and only now, one sentence of disclosure:

> "What we're exploring is a read-only assessment that maps where content from
> a given source actually lives across your AI systems — chunks, embeddings,
> caches, summaries, agent memory — and shows you exactly what's stale or
> orphaned, with evidence you could hand to an auditor."

Then ask for commitment, not opinion:

- "Is that something you'd want run against your environment? What would we
  need to show you first?"
- Budget owners only: "Engagements like that tend to run around $20k for 30
  days, read-only, up to three connectors. Is that in the realm your team
  could approve, and what would getting it through procurement actually look
  like?" (This tests the SPEC §10.5 assessment price band. Log the reaction
  verbatim. Acceptance means "plausible without demanding more than a 25%
  discount"; a request to go materially below that band is a price-band
  failure, not a soft yes.)
- Concrete next-step asks, strongest first: intro to the budget owner (if the
  participant isn't one), a named procurement step (security questionnaire,
  vendor form, PO process), a sanitized lineage map or configuration export
  (seeds the §11.3 requirement), a scheduled follow-up demo slot.

A calendar invite, an intro sent while you're still on the call, or a named
procurement action are commitments. "Sounds interesting, keep me posted" is
not.

---

## 4. Per-interview scoring rubric

Score every interview on these six dimensions within 24 hours. Fields marked
(capture) are the mandatory §11.2 records.

| # | Dimension | Score | Standard |
|---|---|---|---|
| 1 | Qualifying incident | Y / N | Participant described a concrete case — dated, named systems, real people — where one authenticated source lifecycle event (delete/correct/expire/revoke) failed across **≥2 derivative systems outside source control**. Vague "yeah, that's probably happened" = N. Single-store cleanup = N. |
| 2 | Quantified pain | Y / N | Credible numbers on at least one of: people, systems, time, or audit cost, anchored to real events. |
| 3 | Budget owner identified | Y / N + name/role | (capture) A named budget owner, and whether this problem is funded or on an urgent roadmap. Also record deployment owner, mutation approver, and security objections (capture). |
| 4 | Workaround adequacy | Adequate / Strained / Failing | (capture — record the workaround itself either way) Adequate: current approach meets their bar and they're not looking. Strained: works but costly, manual, or nobody trusts it. Failing: known gaps, abandoned fix attempts, or repeat incidents. |
| 5 | Evidence value | High / Medium / Low | High: they currently spend real hours producing propagation/deletion evidence, or have failed to produce it when asked. Medium: occasional requests, tolerable effort. Low: nobody asks them to prove anything. |
| 6 | Pilot willingness | Committed / Interested / None | Committed: took a concrete next step (intro made, procurement step named, artifact promised, follow-up booked). Interested: positive words, no step. None: declined or deflected. |

Additional per-interview flags:

- **Price-band reaction** (budget owners only): accepted / accepted-with-
  discount-≤25% / demanded->25%-discount / rejected / not-asked.
- **Substitute-covered**: their existing platform (Glean, Bedrock KB, BigID,
  native store deletion, internal tooling) plausibly already solves their
  version of the problem. Honest flagging here protects hypothesis 7.
- **Segment**: vertical, company size, stack summary (stores and platforms
  named).

**Credible-buyer definition** (for aggregate scoring): dimension 1 = Y **and**
dimension 3 = Y **and** dimension 6 ≥ Interested.

---

## 5. Aggregate go/no-go thresholds

Scored against 15 completed ICP interviews across ≥10 companies. These mirror
SPEC §11.5 exactly. Per the SPEC, apply one defined two-week repair cycle
before acting on a technical kill; the problem/buyer kills below are market
findings and get no repair cycle — they get a decision.

| Signal (out of 15) | Reading | Decision |
|---|---|---|
| ≥8 qualifying cross-system incidents (rubric #1 = Y) | Broad problem confirmed | Continue broad thesis into days 31–60 |
| 5–7 qualifying incidents | Problem exists but not broadly | Narrow the event type, buyer, or topology wedge before building; re-cut the incident set to find the concentration |
| <5 qualifying incidents | Broad problem thesis unsupported | **Kill broad problem thesis** |
| <5 participants quantify people/systems/time/audit cost (rubric #2) | Pain is anecdotal, not economic | Treat as failing the §11.2 requirement; problem thesis is unproven regardless of incident count — tighten elicitation or accept the narrow/kill reading |
| <3 participants own budget or hold this on an urgent roadmap (rubric #3) | Head of AI Platform is a user, not a buyer | **Kill Head-of-AI-Platform buyer thesis**; re-run buyer validation against security/privacy buyer |
| <3 budget owners accept the test-price band (≤25% discount), or <2 commit to assessment procurement steps | Offer/price mismatch | Rework price and offer before any commercial build |

Interpretation notes:

- The thresholds are conjunctive for a full green light: ≥8 incidents **and**
  ≥5 quantified **and** ≥3 budget owners **and** ≥3 price-band acceptances
  **and** ≥2 procurement commitments. Anything less triggers the specific row
  above, not a judgment call.
- A wave of "substitute-covered" flags among otherwise-qualifying interviews
  is a hypothesis-7 warning even if incident counts pass: it means the pain is
  real but an incumbent already monetizes it. Record it in the day-30 readout.
- Do not average dimensions into a composite score. Each threshold stands
  alone because each kills a different thesis.
- The localhost demo satisfies nothing in this section (SPEC §11.5). Only
  interviews count.

Day-30 readout deliverable: incident case list (one paragraph each),
quantification table, buyer map per company, workaround inventory,
price-band/procurement tally, and the decision row triggered.

---

## 6. Target list

Maintain as a table in this directory (or a sheet mirrored here weekly).
Statuses: `sourced → contacted → screened → scheduled → interviewed → scored`
(plus `disqualified` / `no-response`). Track toward 15 interviews / ≥10
companies; over-source at roughly 3:1 — expect ~45–50 sourced contacts.

| Company | Contact | Role | Segment | Channel | Status | Screener notes | Interview date | Scored (Y/N) |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
| | | | | | | | | |

**Segment suggestions** (aim for spread; no more than ~40% of interviews from
one segment):

- **Mature RAG/agent platform teams** — companies publicly running multiple
  internal copilots or agent products on heterogeneous stores (platform
  engineering blogs, vector-DB case studies, and AI-platform job postings are
  good sourcing signals). Purest test of the broad thesis.
- **Regulated verticals** — financial services, healthcare, insurance, legal.
  Highest expected evidence/audit pain (rubric #5) and clearest budget lines.
- **B2B SaaS handling customer-controlled content** — customer-data deletion
  obligations flow through to AI features; permission-revocation incidents
  likely.
- **Enterprises with heavy knowledge churn** — frequent policy/price/spec
  corrections (e.g., large product or industrial companies with document-heavy
  operations). Best source of correction/expiry incidents.
- **Substitute-anchored teams** (deliberate minority, 1–2 interviews) — fully
  on Glean or Bedrock Knowledge Bases. Included to pressure-test hypothesis 7,
  not to pad the incident count.

**Channels, in rough order of yield:** warm intros from investors/advisors and
former colleagues; practitioner communities (AI platform/MLOps Slack and
Discord groups, meetup speaker lists); conference speaker and podcast guest
lists on RAG-in-production topics; targeted cold outreach referencing the
person's own published talk or post (research framing, never a pitch); vendor
community forums for the stores in our topology (used for sourcing only —
respect community norms, no spam).

Outreach framing template (adapt, keep research-framed, working name only):

> "I'm researching how platform teams handle content deletion, corrections,
> and permission changes across RAG and agent systems — what breaks and what
> it costs to clean up. Your talk on [X] suggests you've lived this. Could I
> get 45 minutes to hear how it works at [company]? Not a sales call; happy
> to share aggregate findings back."
