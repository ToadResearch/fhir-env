# FHIR Query RL

A synthetic EHR benchmark and **Verifiers/PrimeRL environment** for reinforcement learning of FHIR R4 search and **create, read, update and delete** operations. The immediate research question is whether RL improves accurate, efficient querying and requested chart changes. The long-term goal is a general EHR search agent.

This extends the existing benchmark and preserves its CRUD tasks. The current environment is **v0.3.1**, over the frozen **v0.3.0 corpus**. Development is local; no training or external publication is part of this revision. The Python package and loader retain `fhir_workflows` for compatibility; the CLI also exposes `fhir-query-rl`.

## Data and workflows

The [Synthetic Hospital](https://github.com/sparkcpark/synthetic_hospital) expansion contains **1,268 patients**, **50 FHIR resource types**, **139,109 resources excluding Provenance**, and **34,433 tasks in 37 families**. All 5,602 source notes and original profiles are preserved. Authored, seeded episodes add linked longitudinal clinical and administrative records, age-eligible narratives, timestamps, omissions and distractors.

Patient splits are **720 train / 80 dev / 200 public / 268 heldout**. Packaged environment data contains train/dev only. Task counts are correlated instances of shared templates; patient holdout does not establish unseen-workflow generalization.

## How the dataset was built

The synthetic additions come from **hand-authored Python scenario templates and deterministic sampling**. A patient-specific random seed selects age-eligible episodes, values, dates and workflow branches. The generator builds linked FHIR resources and matching narrative notes from those rules. Our augmentation runs locally in Python, without Synthea or LLM calls; upstream notes are preserved separately.

```text
Synthetic Hospital: 1,268 patients + 5,602 encounter notes
             JSON profiles and encounter records
                              |
                +-------------+-------------+
                |                           |
         PRESERVE AND MAP              GENERATE ADDITIONS
         Original notes/profiles       Authored scenarios + patient seed
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
                 Schema/graph checks + reference replay
                              |
                 50 FHIR types / 34,433 base tasks
                 139,109 resources excluding Provenance
```

**All 17,117 Observations, including lab results and measurements, are generated fixtures.** They were not extracted from the source notes. Of the 139,109 non-Provenance resources, **117,428 are generated additions** and **21,681 are source-derived or mixed mappings**. Source notes remain preserved; generated episodes still need clinical review and checks for contradictions with those notes.

The code lives in [the importer and task compiler](environments/fhir_workflows/fhir_workflows/dataset.py), [longitudinal episode templates](environments/fhir_workflows/fhir_workflows/longitudinal.py), and [clinical/administrative scenario templates](environments/fhir_workflows/fhir_workflows/scenarios.py). See the [data card](docs/dataset-card.md) for provenance details.

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

The original 37 families remain available by default. See [the current design](docs/benchmark-design.md), [referral discovery examples](docs/tasks/referrals.md), and [billing discovery examples](docs/tasks/billing.md).

## Tools and metrics

Compare raw FHIR with five optional helpers on identical frozen tasks. Experimental read-only SQL uses the same resources, with FHIR handling writes. A supplied-chart no-tools control covers three read-only families.

Strict success requires a correct answer, retrieval of required evidence fields, and exactly the requested mutations. Ordinary correctness checks retain patient scope, ETags, atomicity, preserved fields and mutation history. Default reward has no retrieval shaping or efficiency bonus.

Metrics record first/all evidence access, model turns and actual API tokens, backend work, opening search behavior, filter novelty, records returned per search and query structure. Offline analysis groups frozen checkpoint evaluations by hop bin, CRUD mix and family, with patient-cluster confidence intervals and explicit censoring/missingness. It can draw the proposed colored difficulty curves from measured rollouts. **Linear or emergent search-budget scaling is a hypothesis, not a measured result here.**

## Local use

```bash
uv pip install -e ./environments/fhir_workflows
fhir-query-rl validate data/synthetic-hospital-v0.3.0 --split dev --discovery-variants
python -m pytest tests -q
```

- [Environment setup, loader arguments and tool contract](environments/fhir_workflows/README.md)
- [Metric definitions and checkpoint analysis](docs/metrics-and-evaluation.md)
- [Resource generation and workflow taxonomy](docs/benchmark-v0.3.0.md)
- [Browse task examples by workflow area](#task-examples)
- [Data card and provenance](docs/dataset-card.md)

Clinical expert review and independent full FHIR/server conformance validation remain outstanding. Reference replay verifies the environment's specified tasks; it does not measure trained-agent capability.
