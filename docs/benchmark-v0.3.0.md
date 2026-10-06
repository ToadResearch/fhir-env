# FHIR Query RL: v0.3.0 clinical and administrative corpus

This revision expands the same 1,268 Synthetic Hospital patients into a broader **research benchmark for chart retrieval and explicitly authorized chart work**. No training is launched by this revision. The generator, verifier and local environment are implemented; clinical review and independent FHIR server testing remain outstanding.

## What changed

| Measure | Previous corpus v0.2.1 | Expanded corpus v0.3.0 |
|---|---:|---:|
| Patients | 1,268 | 1,268 |
| Resource types | 33 | 50 |
| Resources excluding Provenance | 105,066 | 139,109 |
| Total resources including Provenance | 208,864 | 275,651 |
| Task families | 15 | 37 |
| Task instances | 18,386 | 34,433 |

Patient partitions remain **720 train / 80 dev / 200 public / 268 heldout**. The new instance counts are **19,530 / 2,186 / 5,428 / 7,289**, respectively. These are seeded instances of 37 families, not 34,433 independent workflow designs. Family, variant, role, CRUD and depth must accompany any aggregate result.

The corpus includes **13,483 read-only**, **8,311 create/read**, **7,858 read/update**, **2,214 create/read/update**, and **2,567 read/delete** tasks. Deletion is narrowly restricted to explicitly authorized unsubmitted or unsent duplicate drafts. Clinical errors are corrected through status changes and preserved history.

## Workflow coverage

All patients receive registration and scheduling episodes. Each receives three to five age-eligible modules from the remaining eight domains. Consequently a chart is not padded with every possible resource type. The original source profiles, notes and earlier authored longitudinal episodes remain present.

| Domain / staff role | New workflow families | Important distinction |
|---|---|---|
| Registration and front desk | Verified telephone change; interpreter handoff | Preserve other contacts; requesting help does not resolve the support need |
| Scheduling | Reschedule and release old slot; cancel visit and release capacity | Appointment history survives; capacity changes are atomic |
| Laboratory coordination and nursing | Specimen recollection request; corrected result provenance | A rejected new draw is not answered by an old result; collection time differs from report issue time |
| Referral coordination | Imaging report linkage; referral loop closure; acknowledgement plus task completion | Image availability, delivery, clinician acknowledgement and loop closure are different events |
| Medication reconciliation and telephone triage | Supply versus administration; refill routing | Dispensed does not prove taken; unconfirmed instructions do not authorize renewal |
| Immunization nursing and registration | Outside history entry; erroneous vaccination correction | Exact date versus recall; outside source versus direct administration; entered-in-error versus DELETE |
| Insurance verification and billing | Date-specific eligibility; duplicate charge correction; duplicate draft claim deletion | Coverage status versus insurer response for a service date; eligibility is not a payment guarantee |
| Home health and care coordination | Equipment handoff; nutrition-order discrepancy | Delivery versus demonstrated use; an unverified call does not replace a signed order |
| Health information management | Release-scope review; unsent duplicate request deletion | Recipient and date scope versus an active Consent status; preparation is not external disclosure |
| Nursing | Unconfirmed allergy history; measured observation plus visit closure | Preserve unknown drug/date and unconfirmed status; transcribe measured data without inventing interpretation |

New types are **Account, ChargeItem, Claim, CommunicationRequest, CoverageEligibilityRequest, CoverageEligibilityResponse, DeviceRequest, DeviceUseStatement, Flag, ImagingStudy, Immunization, Medication, MedicationAdministration, NutritionOrder, Schedule, Slot, SupplyDelivery**. They are linked to orders, encounters, reports, patients and operational evidence; several earlier types also get new create/update workflows.

## Generating a coherent chart

1. Preserve source text and explicit source profile facts. Do not query hidden diagnosis/answer annotations to enrich visible records.
2. Derive a deterministic patient seed and snapshot from the source timeline. Generate the extension separately from source facts, with origin tags and a provenance ledger.
3. Calculate extension age from the generated date of birth and snapshot, rather than reusing age at the first source encounter. Select age-eligible modules. Very young children do not receive the adult-style medication or injury-referral scenario; infants do not receive the influenza-history module. Caregiver reporting is used in pediatric nursing and contact episodes.
4. Generate linked event sequences, then their notes and workflow requests. Examples include order → specimen rejection → recollection request, or prescription → dispense → declined observed dose. New nursing weight measurements remain close to that patient's generated longitudinal weight.
5. Generate operational uncertainty and distractors: an unavailable appointment slot, an expired release scope with active Consent status, an inactive policy response despite active Coverage, an old corrected laboratory result and a new rejected draw, reported vaccination without an exact date, delivered equipment awaiting teaching, or delivered referral material without acknowledgement.
6. Compile a task's allowed answer, evidence fields and complete state delta. Keep this oracle outside tool-visible resources. Preserve all unrelated state and inspect every committed mutation.

This is an authored extension pipeline, not Synthea output. Synthea remains an optional future source of seed-consistent background events, which would have to pass the same mapping, chronology and contradiction checks. Adding unreviewed bulk resources merely to increase counts is not a realism metric. The source clinical history is preserved, but generated episodes have **not** been checked for every possible contradiction with free-text source notes.

The generator uses verified LOINC identifiers for [HbA1c](https://loinc.org/4548-4/), [creatinine](https://loinc.org/2160-0/), [TSH](https://loinc.org/3016-3/) and [body weight](https://loinc.org/29463-7/). Numerical values and event frequencies are synthetic design parameters, not epidemiological estimates, treatment advice or clinical reference ranges. Other local codes explicitly use a benchmark namespace. No fabricated SNOMED/RxNorm mapping is presented as standardized terminology.

## Research basis and implementation choices

The scheduling design follows the R4 separation between [Appointment](https://hl7.org/fhir/R4/appointment.html), Schedule and Slot. The local simulator adds a deliberately narrow capacity policy: a booked appointment must use a reserved slot within its time interval; unflagged double booking is rejected. This is a benchmark policy, not a claim that all real scheduling systems use these rules.

[AHRQ's referral guidance](https://www.ahrq.gov/cahps/quality-improvement/improvement-guide/6-strategies-for-improving/access/strategy6g-rapid-referral.html) motivates tracking the information exchanged between referring and specialty teams. [AHRQ's discussion of transitions of care](https://psnet.ahrq.gov/primer/discharge-planning-and-transitions-care) motivates the medication, home-care and handoff discrepancies. We implement specific fictional episodes from those broad workflow needs; the sources do not validate our generated patients or branch frequencies.

The data model follows R4's distinctions among [Specimen](https://hl7.org/fhir/R4/specimen.html), [MedicationDispense](https://hl7.org/fhir/R4/medicationdispense.html), [MedicationAdministration](https://hl7.org/fhir/R4/medicationadministration.html), [AllergyIntolerance](https://hl7.org/fhir/R4/allergyintolerance.html), and [CoverageEligibilityResponse](https://hl7.org/fhir/R4/coverageeligibilityresponse.html). Records release uses [Consent](https://hl7.org/fhir/R4/consent.html) with an explicitly fictional local policy. It does not encode legal compliance or a real payer's authorization rules.

## Environment and verification

The environment retains isolated copy-on-write state, bounded REST, optimistic locking, conditional create, pagination and atomic transactions. This revision adds:

- Patient ownership for Appointment participants and Account subjects. Unsupported multi-patient ownership is rejected.
- Explicit per-episode write permission for scheduling Slots; this permission permits status changes only. Other slots stay protected.
- Additional search parameters and includes for the new resources, boolean token matching, and correct medication-code search through Medication references.
- Local checks for appointment/slot intervals and capacity, Observation absent-value contradictions, Immunization occurrence choices, and medication preparation/handover order, in addition to R4 JSON schema and reference integrity.
- A requirement to retrieve case evidence **before** the first write in new scenarios. A write response or a later read cannot retroactively satisfy it. This is a verification rule, not an authorization service for a production EHR.
- A lossless read-only SQL `Resource` projection for every type: `resource_ref`, `resource_type`, `patient_ref`, `resource_json`. `json_extract` is allowed; selecting only IDs cannot satisfy full evidence requirements. Derived tables, subqueries and CTEs are rejected until their field lineage can be verified. Direct base-table joins remain supported; string literals masquerading as column names receive no evidence credit. Typed legacy projections remain available. This is still experimental SQLite, **not a conformant SQL-on-FHIR ViewDefinition implementation**.
- Environment filters `domains`, `roles` and `crud` (required operation letters), alongside family and depth filters.
- Read/write call counts, committed mutations, unique read resources, non-gold read resources and cross-patient read resources. Cross-patient reads can be legitimate during ambiguous-identity searches; the counter is not an automatic failure or a privacy claim.

Existing raw and assisted tool profiles use the same snapshots and verifier. The literal no-tools control remains limited to its three supported read-only families. It cannot perform CRUD and should not be plotted as a matched CRUD baseline. A future no-helper CRUD ablation should use the raw REST profile.

## Difficulty and leakage controls

Most new operational cases provide business identifiers from case paperwork. Those are correctly labeled **one-level retrieval dependencies**, even when many records or mutations are required. A separate resource-relationship graph records the clinical links. The imaging discovery task supplies only the order identifier; report and study identities must be discovered. Its supplied reference plan follows order → report → study, but `ImagingStudy?basedon=...` offers another route; depth 3 is not a mandatory three-stage search. The older one- to four-level task families remain available. Neither a declared DAG nor the supplied reference trace proves a minimal number of API calls: includes, predicates and SQL joins can combine retrieval work.

Conditional prompts present both possible actions and require reading the chart to choose. They do not disclose the chosen authorization, delivery or immunization branch. The nursing-entry task gets its numeric value and timestamps from the signed worksheet. Metadata exposes the same Task write capability for both referral-closure branches.

Write contracts are deliberately structured to make exact state verification reproducible. Many still specify canonical wording and fields. They do not establish general natural-language clinical reasoning, semantic equivalence of arbitrary notes, or performance on unseen workflow designs. Success requires the expected answer, retrieved evidence, correct complete state and acceptable mutation history. Query character length is diagnostic only; longer queries are not intrinsically better.

## Additive environment v0.3.1

The original 37-family corpus remains frozen. Optional `discovery_variants=true` adds 838 train / 89 dev tasks in three families, retaining the same charts and original write contracts. Secondary identifiers are withheld from requests so the agent discovers referral order/report/study and draft-claim coverage/account relationships. These cases have declared depth four and include R, RU, CRU and RD branches. Their additional answer fields and evidence come directly from the frozen resources; no new clinical facts are generated. [The current design](benchmark-design.md) describes these cases, and [the metric protocol](metrics-and-evaluation.md) specifies checkpoint curves by difficulty and CRUD mix.

## Evaluation protocol

Report macro-averaged success by family and domain as well as patient-clustered results. Separate read, create, update, delete and mixed workflows; missing-information branches; and supplied-ID versus discovery cases. Compare paired patient/task snapshots across raw REST, assisted REST and experimental SQL. Measure first/all evidence calls and rounds, failed queries, primitive operations, bytes returned and final state correctness. Count failures as censored search outcomes rather than dropping them from efficiency plots.

The patient split prevents patient overlap, but templates are shared across splits. A claim about unseen-workflow generalization requires a separate family-held-out protocol; this revision does not silently relabel the existing heldout patients as a template-held-out benchmark. Keep train/dev separate from public/heldout model tuning. Deterministic reference replay is compiler verification, not model evaluation.

Before making clinical deployment claims: clinician and clerical review of sample charts and task instructions, independent server/full-validator comparison, clinically meaningful contradiction checking against source narratives, broader identity and terminology variation, and prospective workflow review are still needed.

## Reproduce

```bash
.venv/bin/fhir-query-rl build --source-db sources/hospital/benchmark_v1.3.db --output data/synthetic-hospital-v0.3.0
.venv/bin/python -m pytest tests -q
.venv/bin/fhir-query-rl validate data/synthetic-hospital-v0.3.0 --split train
.venv/bin/fhir-query-rl validate data/synthetic-hospital-v0.3.0 --split dev
.venv/bin/python scripts/benchmark_report.py data/synthetic-hospital-v0.3.0 --output artifacts/benchmark-v0.3.0
.venv/bin/python scripts/package_training_data.py --source data/synthetic-hospital-v0.3.0
```

The output manifest is authoritative for counts and content hashes. The small infrastructure pilot has sixteen artificial patients and is not the Synthetic Hospital corpus. No hosted training or Hub publication is part of this design revision.
