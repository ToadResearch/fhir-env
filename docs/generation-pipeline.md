# Resource generation pipeline

The v0.4.0 pipeline **extends the original charts and every original CRUD family**. It combines their broad workflow coverage with the event-based generation pilot's richer structures. It is designed for FHIR querying and requested chart changes, rather than evaluating clinical judgment.

```text
Synthetic Hospital profiles + encounter notes
                    |
         Preserve source documents exactly
         Keep original mappings and CRUD coverage
                    |
Official FHIR R4 examples + StructureDefinitions
                    |
         Authored episode plans + patient seed
         Age-eligible facts, workflow branches,
         dates, missingness and reporting context
                    |
         Compile linked FHIR records
         Order -> specimen -> report -> results
         Prescription -> dispense -> reported use
         Appointment -> slot -> patient response
         Handoff -> episode/team -> packet -> documents
                    |
         Merge with the existing chart
         Shared facilities; separate origin/history ledger
         Matching synthetic coordination notes
                    |
         Derive evidence and CRUD contracts from records
         Refresh exhaustive queries against expanded charts
                    |
         R4 schema + nested structure/reference checks
         Workflow checks + EVERY task reference replay
                    |
         Immutable, checksummed v0.4.0 snapshot
         Independent R4 validator + manual review before release
```

**How data is generated:** human-authored Python rules plus deterministic sampling. No Synthea or LLM service is invoked. Official examples supply selected measurement structures and codes; they do not generate patient histories. Source notes are preserved, not automatically converted into new lab measurements. Each synthetic episode is explicitly labeled.

| Improvement | What changes |
|---|---|
| Coverage | Retains all 37 original families and adds 10; keeps all 50 types and adds `AppointmentResponse`, `HealthcareService`, `DocumentManifest`, `DetectedIssue` |
| Laboratory structure | Final, pending, rejected and corrected branches; independent measurements, components and `hasMember` panels; explicit specimen/order/report links |
| Timeline integrity | Seeded timing variation; collection before receipt/report; correction versions preserved; pharmacy preparation before handover; appointments consistently in the future |
| Partial information | Unknown medication use, missing reported weight/contact information, structured caller reports and unconfirmed receipt remain distinct from established observations |
| Clinical and clerical context | Caregiver reporting for children, coordination notes, shared clinic/lab/payer resources, transfer packets and administrative order review |
| CRUD | Read-only search, follow-up creation, appointment/slot/response updates, atomic transmission logging and Task completion, plus original authorized draft deletions |
| Query robustness | Explicit bounded reference pages, date-targeted message search, source-import `_tag` filtering, panel-member includes, scalar Identifier support |
| Validation | Nested R4 cardinalities, choice exclusivity and reference targets; graph/workflow checks; replay before publishing the output directory |

The source-import scope matters: new medication reports must not turn “no imported source-profile medications” into a claim that no medication records exist. Longitudinal result gold sets are recalculated when added records fall within the requested interval. Original task identities and patient partitions are retained.

Two original R4 defects are repaired explicitly: reported source-profile allergies receive active/unconfirmed chart-list status, and invalid `DeviceUseStatement.basedOn -> DeviceRequest` links move into Provenance. The latter is required because [R4 permits ServiceRequest as that field's target](https://hl7.org/fhir/R4/deviceusestatement.html). Original profile and note attachments remain exact; repairs are listed in a sidecar.

Episode plans, creation/update events, prior versions, repairs and scoring keys are stored outside the agent-visible chart. Current records carry origin tags and ordinary Provenance. The episode ledger explains what was generated; it does not establish every clinical fact in an upstream note. History sidecars do not imply that the environment supports the FHIR `_history` endpoint.

## Measured comparison

Same 1,268 patients and partitions; every original task family and resource identity retained:

| Measure | Original | Enhanced |
|---|---:|---:|
| Resources excluding Provenance | 139,109 | 189,347 |
| Resource types | 50 | 54 |
| Task instances / families | 34,433 / 37 | 47,113 / 47 |
| Distinct field shapes | 88 | 138 |
| Reference edge types | 108 | 151 |
| Independent full-dev R4 errors | 67 | 0 |

All 47,113 reference plans passed. Independent validation covers the same 80 dev patients: 17,460 original versus 23,716 enhanced resources, including Provenance. It retains 40,436 warnings in the enhanced charts; terminology is disabled. The complete corpus is checked with schema/contracts/graph validation and reference replay, while independent Java validation covers dev.

A second comparison controls record counts: **24 identical dev patients, 12 common resource types, 1,155 records per side**, with 100 seeded resamples. Mean distinct shapes increase **37 → 69.38**, selected query features **22 → 30.95**, and non-patient reference edges **1,492 → 1,656.03**. These are descriptive results on this cohort, not significance tests or estimates of real-EHR performance. Types/counts are matched; status/representation mixtures intentionally differ.

Manual inspection covered eight actual dev cases: adult and child examples for each lab state, plus linked medication, appointment, transfer and intake records. Unknown use/receipt stayed explicit, rejected/pending reports did not acquire an older order's results, and caregiver reporters were used for children. Notes remain authored templates; this is not clinical expert review. [Examples](tasks/records.md), [all resource counts](resource-counts-v0.4.0.md), and local `artifacts/generation-v0.4.0/review-pack.md` make the records inspectable.

## Reproduce and inspect

```bash
# Extend an existing frozen snapshot; output must be a new directory.
fhir-query-rl enhance \
  --source data/synthetic-hospital-v0.3.0 \
  --output data/synthetic-hospital-v0.4.0 --seed 17 --workers 8

# Or build directly from the original SQLite import.
fhir-query-rl build --source-db sources/hospital/benchmark_v1.3.db \
  --output data/new-v0.4.0 --pipeline enhanced --workers 8

python scripts/review_generation.py
python scripts/validate_generation.py
python -m pytest tests -q
```

The CLI's `build` defaults to the enhanced pipeline; `--pipeline legacy` reproduces the old compiler. Standard generation and reference replay need no network or model credentials. Independent validation uses a pinned [HL7 validator release](https://github.com/hapifhir/org.hl7.fhir.core/releases/tag/6.10.4), Java and cached FHIR packages; a fresh validator installation needs package downloads. `-tx n/a` disables terminology validation. The JSON contracts can be rebuilt from the official `hl7.fhir.r4.core#4.0.1` package with `scripts/build_resource_contracts.py`.

The generated dataset is under `data/`, which is Git-ignored. The pipeline, contracts, documentation and tests are the reproducible source. `artifacts/generation-v0.4.0/` contains the comparison, complete review excerpts, validator inputs/results and checksums. Train/dev can be bundled locally with `scripts/package_training_data.py`; public and heldout charts must remain outside that package.

## What “better” means here

Compare source preservation, resource/task coverage, field shapes, reference relationships, state variants and actual conformance errors on the same patient cohort. Whole-corpus diversity increases partly because records are added; it is not a count-controlled causal result. The earlier [pilot comparison](generation-pilot.md) supplies the smaller controlled experiment.

Executable reference plans measure whether specified tasks can be completed correctly in this environment. They do not show a model learning, fewer tokens/turns, clinical validity or hospital deployment readiness. Terminology, US Core, independent-server replay and clinician review remain separate checks. This revision launches no training and publishes nothing externally.
