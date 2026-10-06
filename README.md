# FHIR Query RL

A synthetic EHR benchmark and **Verifiers/PrimeRL environment** for reinforcement learning of FHIR R4 search and **create, read, update and delete** operations. The immediate research question is whether RL improves accurate, efficient querying and requested chart changes. The long-term goal is a general EHR search agent.

This extends the existing benchmark and preserves its CRUD tasks. The current environment and corpus are **v0.4.0**; the original **v0.3.0 corpus** remains an unchanged comparison. Development is local; no training or external publication is part of this revision. The environment directory and Python package are `fhir_query_rl`; the CLI and distribution name are `fhir-query-rl`.

The package was renamed from `fhir_workflows`. Install from `environments/fhir_query_rl` and import `load_environment` from `fhir_query_rl`. Existing `https://fhir-workflows.example/...` identifiers remain stable inside the frozen charts; they are data namespaces, not package paths.

## Data and workflows

The [Synthetic Hospital](https://github.com/sparkcpark/synthetic_hospital) expansion contains **1,268 patients**, **54 FHIR resource types**, **189,347 resources excluding Provenance**, and **47,113 tasks in 47 families**. All 5,602 source notes and original profiles are preserved. Authored, seeded episodes add linked longitudinal clinical and administrative records, age-eligible narratives, timestamps, omissions and distractors.

Patient splits are **720 train / 80 dev / 200 public / 268 heldout**. Packaged environment data contains train/dev only. Task counts are correlated instances of shared templates; patient holdout does not establish unseen-workflow generalization.

The full generated dataset is excluded from Git, including `data/` and the local environment's `training_data/` copy. Source downloads, validation artifacts and built distributions are also ignored. Only the small synthetic `pilot/` test fixture is committed. A fresh clone needs a [local data build](docs/generation-pipeline.md#reproduce-and-inspect) for the full benchmark; verified train/dev data can then be bundled with `python scripts/package_training_data.py`.

## How the dataset was built

The synthetic additions come from **hand-authored Python scenario templates and deterministic sampling**. A patient-specific random seed selects age-eligible episodes, values, dates and workflow branches. The generator builds linked FHIR resources and matching narrative notes from those rules. Our augmentation runs locally in Python, without Synthea or LLM calls; upstream notes are preserved separately.

```text
Synthetic Hospital: 1,268 patients + 5,602 encounter notes
             JSON profiles and encounter records
                              |
                +-------------+-------------+
                |                           |
         PRESERVE AND MAP              GENERATE ADDITIONS
         Original notes/profiles       Authored event plans + patient seed
         -> DocumentReference          -> Labs, orders, reports, messages,
         Explicit facts/visits            medications and admin workflows
         -> Encounter, Condition,      -> Matching synthetic notes,
            MedicationStatement, ...      demographics and identifiers
                |                           |
                +-------------+-------------+
                              |
                 Merge into linked FHIR charts
                 Record origin tags and provenance
                              |
                 Compile requests, required evidence,
                 expected answers and CRUD changes
                              |
                 R4 structural/graph checks + EVERY reference replay
                 Independent dev validation + manual inspection
                              |
                 54 FHIR types / 47,113 base tasks
                 189,347 resources excluding Provenance
```

**All 24,651 Observations, including lab results and measurements, are generated fixtures.** They were not extracted from the source notes. Of the 189,347 non-Provenance resources, **167,666 are generated additions** and **21,681 are source-derived or mixed mappings**. Source notes remain preserved; generated episodes still need clinical review and checks for contradictions with those notes.

The current augmentation pipeline lives in [generation.py](environments/fhir_query_rl/fhir_query_rl/generation.py), with [FHIR structure contracts](environments/fhir_query_rl/fhir_query_rl/resource_contracts.py). It extends [the original importer and task compiler](environments/fhir_query_rl/fhir_query_rl/dataset.py), [longitudinal episode templates](environments/fhir_query_rl/fhir_query_rl/longitudinal.py), and [clinical/administrative scenario templates](environments/fhir_query_rl/fhir_query_rl/scenarios.py). See the [data card](docs/dataset-card.md) for provenance details.

The [new pipeline and measured comparison](docs/generation-pipeline.md) combines the original coverage with the pilot’s standards-derived structures: component/member panels, correction history, unknown reports, shared facilities and multi-hop transfer packets. It retains every original family and adds ten. All **47,113 reference plans pass**; independent R4 validation of complete dev charts finds **0 errors versus 67 originally**, with terminology disabled and warnings remaining. These checks establish benchmark executability, rather than trained-model or clinical performance. [Resource counts](docs/resource-counts-v0.4.0.md) show all types and totals.

### Reproducibility

**Data generation is deterministic** given identical source data, seed, generator code, runtime and FHIR definitions. Resources, synthetic narratives, tasks, gold answers and history sidecars reproduce identically; repeated-build tests compare manifests and file checksums. Parallel workers preserve output ordering. Manifests record input/output checksums and generator/definition hashes. Model rollouts, training results and validator execution times are not guaranteed to be deterministic.

To reproduce the current augmentation from the preserved baseline, use a new output directory:

```bash
fhir-query-rl enhance \
  --source data/synthetic-hospital-v0.3.0 \
  --output data/reproduced-v0.4.0 --seed 17 --workers 8
```

## Task examples

| Area | Examples | Operations |
|---|---|---|
| [Clinical search](docs/tasks/clinical-search.md) | Latest valid results, serial measurements, order → report → imaging study | R |
| [Nursing](docs/tasks/nursing.md) | Measured weight and visit closure; uncertain reported allergy | R → C/U |
| [Medications](docs/tasks/medications.md) | Supply versus administration; refill routing | R / R → C |
| [Laboratory](docs/tasks/laboratory.md) | Corrected results; rejected-specimen recollection | R / R → C |
| [Referrals](docs/tasks/referrals.md) | Discover linked evidence, check acknowledgement, complete follow-up and log it | R → U/C |
| [Scheduling](docs/tasks/scheduling.md) | Reschedule or cancel while releasing/reserving capacity atomically | R → U |
| [Registration](docs/tasks/registration.md) | Verified contact changes; interpreter handoff | R → C/U |
| [Insurance](docs/tasks/insurance.md) | Coverage transitions; date-specific eligibility | R / R → C/U |
| [Billing](docs/tasks/billing.md) | Duplicate charge correction; authorized duplicate draft removal | R → U/D |
| [Records](docs/tasks/records.md) | Consent-scope review; records-release preparation | R → C/D |
| [Evidence collection](docs/tasks/evidence-collection.md) | Documented prerequisites and gaps against a fictional prior-auth policy | R |

Patient reports, delivery, dispensing, acknowledgement and confirmed observations remain distinct. Missing records support “not documented,” rather than an invented clinical conclusion. Clinical cancellations preserve history through status changes; literal deletion is limited to authorized duplicate drafts.

## Multi-hop extension

`discovery_variants=true` adds **838 train and 89 dev cases in three additional families**, using the same charts and original allowed mutations. The agent must discover secondary references rather than receive their business identifiers. These variants include referral closure, atomic acknowledgement logging plus Task completion, and insurance/account checks before draft-claim deletion. Their declared discovery depth is four; includes, chaining and joins may combine calls.

All 47 base families remain available by default. See [the current design](docs/benchmark-design.md), [referral discovery examples](docs/tasks/referrals.md), and [billing discovery examples](docs/tasks/billing.md).

## Tools and metrics

Compare raw FHIR with five optional helpers on identical frozen tasks. Experimental read-only SQL uses the same resources, with FHIR handling writes. A supplied-chart no-tools control covers three read-only families.

Strict success requires a correct answer, retrieval of required evidence fields, and exactly the requested mutations. Ordinary correctness checks retain patient scope, ETags, atomicity, preserved fields and mutation history. Default reward has no retrieval shaping or efficiency bonus.

Metrics record first/all evidence access, model turns and actual API tokens, backend work, opening search behavior, filter novelty, records returned per search and query structure. Offline analysis groups frozen checkpoint evaluations by hop bin, CRUD mix and family, with patient-cluster confidence intervals and explicit censoring/missingness. It can draw the proposed colored difficulty curves from measured rollouts. **Linear or emergent search-budget scaling is a hypothesis, not a measured result here.**

## Local use

```bash
uv pip install -e ./environments/fhir_query_rl
fhir-query-rl validate data/synthetic-hospital-v0.4.0 --split dev --discovery-variants
python -m pytest tests -q
```

- [Environment setup, loader arguments and tool contract](environments/fhir_query_rl/README.md)
- [Metric definitions and checkpoint analysis](docs/metrics-and-evaluation.md)
- [Resource generation and workflow taxonomy](docs/benchmark-v0.3.0.md)
- [Browse task examples by workflow area](#task-examples)
- [Data card and provenance](docs/dataset-card.md)

Clinical expert review, terminology/US Core validation and independent-server execution remain outstanding. Reference replay verifies the environment's specified tasks; it does not measure trained-agent capability.
