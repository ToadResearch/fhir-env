---
language:
- en
tags:
- fhir
- synthetic-ehr
- reinforcement-learning
task_categories:
- question-answering
pretty_name: FHIR Query RL
---

# FHIR Query RL dataset card (draft; unpublished)


## Current v0.4.0 expansion

The current corpus retains all 1,268 patients and original partitions, all 6,870 exact source documents (5,602 encounter notes and 1,268 profiles), every original resource identity and all 37 original task families. It adds event-based structures and ten task families: **54 types, 189,347 non-Provenance resources, 185,512 Provenance records and 47,113 tasks**. Counts are **26,730 train / 2,986 dev / 7,428 public / 9,969 heldout**. The environment's packaged charts contain train/dev only.

All 24,651 Observations are generated fixtures. Added panels, specimen workflows, medication handoffs, patient responses, transfer packets and coordination notes use authored deterministic rules and official R4 patterns; no Synthea or model-generation service is used. Separate plans, histories and origin records support inspection. Source profiles/notes remain exact; explicit conformance repairs affect 403 reported allergy mappings and 666 device-order links. See [generation and limitations](generation-pipeline.md) and [counts by resource](resource-counts-v0.4.0.md).

Every one of the 47,113 reference plans passes the strict verifier. Independent base R4 validation of full dev charts, with terminology disabled, reports zero errors across 23,716 resources; the original full dev charts report 67 errors across 17,460 resources. Warnings remain. These are structural/executability checks, not observed model performance, clinical adjudication or US Core certification.

## Frozen v0.3.0 comparison corpus

The frozen v0.3.0 comparison contains 1,268 Synthetic Hospital patients expanded with explicitly fictional FHIR R4 workflow episodes. It retains original notes and explicit profiles. Generated names, dates of birth, identifiers, providers, administrative records and clinical workflow facts are distinguished by metadata tags and a sidecar ledger. Synthetic Hospital already has a FHIR simulator; this derivative adds transactional workflow graphs and a new deterministic task compiler.

Patient splits: 720 training and 80 development, both from upstream train; 200 public and 268 upstream heldout. Task counts: 19,530 training, 2,186 development, 5,428 public, 7,289 heldout. There are 37 task families. Patient/template instances are correlated; these counts do not represent 34,433 independently designed workflows. Statistical inference should resample patients/source clusters.

The corpus has 275,651 resources of 50 types, including 136,542 Provenance resources. Excluding provenance leaves 139,109 resources (about 110 per patient). The original longitudinal expansion is retained. New modules cover registration, scheduling, diagnostics, referrals, medication reconciliation, immunizations, insurance and billing, equipment and nutrition handoffs, records release, and nursing documentation. All patients receive registration/scheduling and a seeded subset of three to five age-eligible additional modules. Pediatric nursing and telephone reporting use caregivers, and new nursing weight is correlated with prior generated measurements.

The benchmark includes state-dependent branches: unknown versus documented dates, delivery without demonstrated use, expired authorization, insurer responses that differ from a registration status, and delivered material without acknowledgement. CREATE includes AllergyIntolerance, Immunization, Observation, Communication and workflow Task records; UPDATE preserves unrelated fields; literal DELETE is limited to explicitly authorized draft duplicates. See [the workflow taxonomy and research](benchmark-v0.3.0.md) for exact families, resource additions, metrics and limits. These are authored synthetic episodes, not clinician-validated histories or representative workflow frequencies.

All 5,602 source encounter notes are preserved verbatim inside DocumentReference attachments. Source conditions and home medications are imported as text concepts rather than invented codes. Negative allergy statements remain in the exact source-profile document; they are not converted into positive allergy records. Hidden original benchmark diagnosis labels and ontology answer annotations are excluded from visible resources. Original source notes are synthetic and can contain artifacts and contradictions; this import does not adjudicate them.

New clinical facts, including the lab result and fictional prerequisite procedure, are generated workflow fixtures. Their presence and record-omission interventions are known by construction. The latent synthetic ledger establishes what the generator added or withheld; it does not establish all real-world clinical facts for the original source patients. Accessible-record absence must be described as unavailable/not documented, not absence of disease or an event. Present/absent documents and recent/stale prerequisite evidence vary across patients.

The in-process environment supports a documented FHIR REST subset, isolated writes, ETags, transaction rollback, idempotent creation and selected SQL read projections. Gold answers, required evidence fields, state deltas, omissions, and reference traces are kept outside the agent-visible chart. Default packaged training excludes public and heldout resources. Source data, generated data, scoring keys, and task prompts should be distributed as separate configurations in an eventual HF release.

Validation includes the official FHIR R4 4.0.1 JSON schema, local reference/patient integrity, deterministic compilation, automated reference traces and negative verifier tests. It does not yet include the official Java validator's complete FHIRPath/terminology checks, US Core conformance, independent HAPI/server replay, clinician review, actual trained-agent performance, privacy deployment validation, or a compliant Da Vinci authorization exchange. SQL projections are experimental and do not claim SQL on FHIR conformance.

The local v0.3.1 environment optionally appends 838 train and 89 dev discovery tasks in three families. They use these same resources and splits, retain the original allowed mutations, and add evidence/answer fields read from the frozen chart. Base corpus counts and checksums remain unchanged. See [the current design](benchmark-design.md) and [evaluation protocol](metrics-and-evaluation.md).

Intended use is synthetic research on EHR retrieval and requested chart changes. Do not use for patient care, diagnosis/treatment decisions or autonomous insurance decisions. Local retrieval does not guarantee that information forwarded to a cloud model is free of PHI in a future real-EHR setting.

Source: [Synthetic Hospital data card](https://github.com/sparkcpark/synthetic_hospital/blob/main/DATA_CARD.md), pinned source commit `911f34c4ac65a508543c4b3b90c373a0cd16534d`. Upstream code is MIT; the data card describes fully synthetic redistributable data and specific terminology caveats. This importer does not copy the ontology tables or SNOMED descriptions. The official schema and LOINC example code require source attribution. Select and record the generated derivative's explicit license before public publication; no license is silently assigned to the user's new project.
