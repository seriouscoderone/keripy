# The Ecosystem Pattern

A portable, domain-driven reference for designing any cooperative ecosystem — from a regulated identity framework like vLEI down to a homeschool co-op. It generalizes the structural shape implicit in the Trust over IP (ToIP) governance stack and the GLEIF vLEI Ecosystem Governance Framework into vocabulary you can hand to a working group on day one.

This document is **not** a KERI document. It is the abstract pattern. KERI / ACDC / ToIP are *one technology stack* an ecosystem may choose to run on; the pattern stands without them.

---

## 1. What is an "ecosystem"?

> An **ecosystem** is a bounded context in which multiple parties cooperate around a shared **purpose**, under a shared **governance framework**, by exchanging verifiable **attestations** that let them do business with each other without needing to trust each other personally.

That definition fits every concrete instance — vLEI, USA Gymnastics, NAIC + state insurance regulators, ASE-certified mechanics, an organic-farming co-op, a homeschool co-op, a CrossFit affiliation. The differences are populations of slots, not different shapes.

---

## 2. The frame: three pillars, two loops, two spans of control

Every ecosystem decomposes along three orthogonal concerns:

- **Purpose & Scope** — *why* the ecosystem exists and who it covers.
- **Governance** — the rules and the governors of the rules.
- **Technology** — the rails the rules run on.

…and operates as two coupled feedback loops:

- **Define loop**: Risk Assessment → Governance Requirements → Governance Framework → Residual Risk Assessment. This *writes* the rules.
- **Run loop**: Governing Parties bind Governed Parties via Governance Agreements; Auditors and Certification Bodies provide independent oversight. This *executes* the rules and feeds evidence back into Define.

Two spans of control sit side by side and exchange contracts:

- The **ecosystem's** span — purpose, roles, attestations, policies, liability, recognition.
- The **technology framework's** span — protocol layers, registries, wallets, signatures.

The ecosystem *uses* technology; the technology *learns from* ecosystems. Treating them as separate bounded contexts (not one stack) is the single most important architectural call you can make.

---

## 3. The ten core elements (irreducible)

If someone asks "what does an Ecosystem Governance Framework consist of?", the irreducible core is:

| # | Element | One-line gloss |
|---|---|---|
| 1 | **Purpose & Scope** | What we're doing and for whom. |
| 2 | **Authority Root + Delegation Tree** | Who decides, and how decision rights flow downward. |
| 3 | **Roles & Membership Policies** | Typed parties; how you become / stop being one. |
| 4 | **Attestation Catalog** | What verifiable claims circulate, with schemas and disclosure modes. |
| 5 | **Trust Registry** | The discoverable canonical list of who and what is legitimate. |
| 6 | **Risk Register & Information Trust Policies** | What could go wrong; how we prevent and detect it. |
| 7 | **Liability & Remedy Model** | Who pays when it does go wrong; how disputes resolve. |
| 8 | **Legal & Commercial Models** | The contracts that bind parties and the value flows that sustain them. |
| 9 | **Compliance & Mutual-Recognition Posture** | How we sit relative to outside regulators and neighbor ecosystems. |
| 10 | **Audit & Assurance Regime** | The independent eyes. |

Every concrete ecosystem is a population of these ten slots. An apparently-empty slot (e.g., a homeschool co-op with no formal Risk Register) is itself a design observation — usually a fragility.

---

## 4. Ubiquitous language

DDD-shaped vocabulary. Use these terms verbatim with stakeholders. They displace ad-hoc jargon ("standards body", "membership program", "rulebook") with precise concepts.

### 4.1 Aggregates (things with identity and a lifecycle)

| Term | Definition |
|---|---|
| **Ecosystem** | The bounded context itself — the named cooperative arena. |
| **Charter** *(Primary Governance Document)* | The constitutional doc: ecosystem name, purpose, authority root, scope. |
| **Governance Framework** | The full document corpus: charter + policies + requirements + agreements + appendices. |
| **Risk Register** | Standing inventory of identified risks, owners, mitigations, residuals. |
| **Trust Registry** | Discoverable, authoritative list of legitimate participants and the artifacts they may issue. |
| **Membership Roll** | Bound state of who is currently a Governed Party, in what status. |
| **Attestation Schema** | The *type* of a verifiable claim: fields, issuer rules, validity rules, disclosure mode. |
| **Attestation** | An *instance* of a schema, asserted by an Issuer about a Subject. |
| **Delegation** | A scoped, time-bounded grant of authority from one role to another. |
| **Dispute** | A contested event with a remedy path defined by the framework. |

### 4.2 Value objects (defined by attributes, not identity)

| Term | Definition |
|---|---|
| **Purpose Statement** | One paragraph: why the ecosystem exists. |
| **Scope** | What's in / out — who is bound, where, for what activities. |
| **Authority Root** | The apex Governing Party from which all delegation descends. |
| **Liability Boundary** | A line drawn between two roles allocating who is on the hook for what. |
| **Disclosure Mode** | Full / selective / partial — how much of an attestation must be revealed in a presentation. |
| **Trustmark** | The visible symbol of "in good standing" membership (logo, badge, seal). |
| **Recognition Rule** | Condition under which an attestation from a *neighboring* ecosystem is accepted here. |
| **Compliance Regime** | External regulators / standards this ecosystem subordinates itself to. |
| **Commercial Term** | How value flows: fees, dues, premiums, dividends, royalties, mutual aid. |

### 4.3 Roles (typed parties)

A single real-world organization usually plays several of these. Roles are *capacities*, not party identities.

| Role family | Sub-roles | Plain-language gloss |
|---|---|---|
| **Governing Parties** | Authority Root, Governance Body, Sub-Committee | "Make the rules" |
| **Governed Parties** | Member, Subject | "Bound by the rules" |
| **Issuance roles** | Issuer, Authorizer, Sub-Issuer (delegated) | "Make the claims" |
| **Custody / use roles** | Holder, Subject (when distinct from Holder), Presenter | "Carry & show the claims" |
| **Verification roles** | Verifier, Relying Party | "Check & depend on the claims" |
| **Independent oversight** | Auditor, Certification Body, Ombudsperson | "Check the checkers" |
| **Operations** | Infrastructure Operator, Registry Operator | "Run the rails" |

### 4.4 Domain events (the verbs that move state forward)

These are how the Define loop and Run loop talk to each other.

- `EcosystemChartered` / `CharterAmended`
- `RiskIdentified` / `RiskMitigated` / `RiskAccepted`
- `MemberAdmitted` / `MemberSuspended` / `MemberExpelled`
- `RoleAccredited` / `AccreditationRevoked`
- `DelegationGranted` / `DelegationScopeNarrowed` / `DelegationRevoked`
- `AttestationSchemaPublished` / `SchemaDeprecated`
- `AttestationIssued` / `AttestationPresented` / `AttestationVerified` / `AttestationRevoked`
- `AuditPerformed` / `CertificationGranted`
- `DisputeRaised` / `DisputeResolved`
- `RecognitionEstablished` / `RecognitionWithdrawn`
- `LiabilityClaimed` / `IndemnityPaid`

---

## 5. Context map (DDD)

Treat **Ecosystem** as the root bounded context. Its neighbors and the relationship type:

| Neighbor | DDD relationship | What crosses the boundary |
|---|---|---|
| **Member context** (each member's internal world — their wallet, their books) | Customer / Supplier (ecosystem upstream) | Attestations, membership status, dues |
| **Regulator context** | Conformist | Compliance obligations, audit findings |
| **Adjacent Ecosystem context** | Partnership | Recognized attestations, mutual-recognition contracts |
| **Technology Stack context** (ToIP layers, KERI, an industry XML schema, etc.) | Open Host Service | Protocol contracts, schemas, identifiers |
| **Auditor context** | Anti-corruption layer | Run-loop evidence translated into Define-loop input |

This map is the same whether the ecosystem is GLEIF or a homeschool co-op. Only cardinality and formality change.

---

## 6. Design worksheet

A working group can answer these in order to populate the framework. Each question maps to one of the ten core elements.

1. **Purpose & Scope** — In one paragraph, why does this ecosystem exist? Who is in scope; who is explicitly out?
2. **Authority Root** — Who is the apex governing party? How is it constituted? How is it itself accountable? *(GLEIF has the ROC; a co-op has its members; a fitness federation has an elected board; there is always something above the apex.)*
3. **Delegation Tree** — From the root, what authority descends to whom, with what scope, with what depth limit, and how is it revoked?
4. **Roles** — Enumerate every typed capacity. For each: what attestations does it issue, hold, present, verify? What governance obligations bind it?
5. **Membership Lifecycle** — How does a party become a Member? How is membership suspended, terminated, restored? What evidence is required at each transition?
6. **Attestation Catalog** — For each attestation: schema, issuer role, holder role, verifier roles, disclosure mode, chaining (does it depend on the issuer holding another attestation?), validity period, revocation policy.
7. **Trust Registry** — Where is the canonical list of legitimate participants and schemas? Who operates it? How is it updated? What does "discoverable" mean here?
8. **Risk Register** — What can go wrong? For each: likelihood, impact, owner, mitigation, residual risk. *(A framework without a risk register is a wish list.)*
9. **Information Trust Policies** — Security, privacy, availability, confidentiality, processing-integrity policies. What data classes exist; what controls apply to each?
10. **Liability & Remedy** — For each pair of roles where harm could flow, where is the boundary? What evidence establishes fault? What is the remedy (arbitration, indemnity, expulsion, regulatory referral)?
11. **Legal Model** — What contract binds Members to the framework? How is the framework itself amended? How is consent re-obtained on amendment?
12. **Commercial Model** — How does the ecosystem fund itself? How does value flow between roles? Is this fee-for-service, mutual, subsidized, regulated?
13. **Compliance Regime** — Which external regulators apply? How does the framework demonstrate compliance to each?
14. **Mutual Recognition** — Which neighbor ecosystems do we recognize? Under what rule? Who decides to extend or withdraw recognition?
15. **Audit & Assurance** — Who audits whom, how often, under what standard? How are findings published? What happens to findings — risk register update, framework amendment, member sanction?

Answer all fifteen and you have an ecosystem governance framework. Skip any and you have a fragility — name it.

---

## 7. Portability test — same shape, four ecosystems

| Slot | vLEI | Fitness federation | Automotive (US) | Homeschool co-op |
|---|---|---|---|---|
| Authority Root | GLEIF (overseen by ROC) | Federation board | NHTSA + State DMV | Co-op leadership / state HS law |
| Issuer (apex) | Qualified vLEI Issuer | Certifying body / master coach | DMV + manufacturer | Parent-teacher / evaluator |
| Subject | Legal Entity | Member athlete | Vehicle / driver | Student / family |
| Headline attestation | Legal Entity vLEI | Trainer cert / belt rank | Title + driver license | Course completion / transcript |
| Trust Registry | Schema Registry + QVI list | Certified-trainer roster | VIN / ASE registries | Member directory |
| Risk Register | GLEIF risk register | Injury-liability log | Recall + crash data | Safety / curriculum risks |
| Liability model | Issuer / LE contractual terms | Waiver + federation insurance | Manufacturer / driver / insurer split | Indemnity waivers |
| Mutual recognition | Global LEI system, jurisdictional | Reciprocal gym access; cross-org cert acceptance | Interstate license reciprocity | College admissions accepting transcripts |
| Commercial model | QVI fees + GLEIF funding | Dues + cert fees | Sale + service + premium | Co-op fees + shared tuition |
| Compliance regime | EU/US financial regulators via ROC | Self-regulatory or none | DOT / EPA / state | State homeschool statute |

The vocabulary in §4 lets a stakeholder from any of these four conversations talk to a stakeholder from any other and understand each other within minutes. That is the test of a good ubiquitous language.

---

## 8. Anti-patterns and smells

Patterns to *avoid* — each one is a real failure mode observed in actual ecosystems.

- **Conflated authority and operations.** The body that *writes* the rules also *runs* the registry and *audits* compliance. Separation of Governing / Operating / Auditing is the cheapest insurance against capture.
- **Implicit Authority Root.** "Everyone just knows" who decides. Works in a five-person co-op for six months. Falls over the moment a hard call has to be made.
- **Attestation catalog without disclosure modes.** Privacy is bolted on later, expensively, after a breach.
- **Delegation without depth limits or revocation procedures.** Authority leaks. Who deputized whom becomes unanswerable.
- **No Risk Register.** Every ecosystem has risks; an ecosystem without a register has *unmanaged* risks.
- **No mutual-recognition rule.** Either you reject every external attestation (and re-credential everyone yourselves, expensively) or you accept whatever shows up (and inherit every neighbor's failures).
- **Tying the framework to a specific technology stack.** The framework should outlive any one protocol generation. The technology stack is a downstream choice that the framework *uses*; it should not appear in the charter.
- **Members who are also auditors of themselves.** Independent oversight requires independence.
- **No amendment procedure.** Frameworks that cannot evolve calcify and get worked around.
- **Commercial model implicit or absent.** Ecosystems without a sustainable funding model degrade silently.

---

## 9. How to use this document

- **Designing a new ecosystem from scratch:** work the §6 worksheet top-to-bottom with the relevant stakeholders. Use the §4 vocabulary in every meeting. Produce a charter + the controlled documents implied by your answers.
- **Auditing an existing ecosystem:** for each of the ten core elements in §3, ask "where is this written down, and is it current?" Missing or stale answers are findings.
- **Mapping two ecosystems for mutual recognition:** lay each one's §4.1 aggregates side by side. The recognition rule is a contract over the *attestation schemas* and the *trust registries* — not over the underlying parties.
- **Choosing technology:** only after §6 is answered. The framework's data and authority shape determines the technology fit, not the other way around.

---

## 10. Provenance

This pattern abstracts the structural shape common to:

- The Trust over IP (ToIP) governance stack — three pillars and the Define / Run loops.
- The GLEIF vLEI Ecosystem Governance Framework — concrete instantiation with a Primary Document, Controlled Documents, Trust Assurance Framework, Risk Assessment, Information Trust Policies, and a delegation chain from GLEIF root to QVIs to Legal Entities.
- Domain-Driven Design — the shaping of the vocabulary as Aggregates, Value Objects, Roles, and Domain Events, and the use of a Context Map for inter-ecosystem relationships.

It is intentionally technology-agnostic. KERI / ACDC / X.509 / ISO 17442 / a paper roster in a binder are all valid implementation choices for the rails.
