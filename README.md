# FHIR Workflows

A synthetic EHR search and CRUD research benchmark, packaged as a Prime Intellect/Verifiers environment. The local environment and expanded corpus are v0.3.0; the previously published environment remains v0.2.5. This pass focuses on benchmark/environment design and launches no training. See [the v0.3 design and research](docs/benchmark-v0.3.0.md). Start with [the benchmark design](docs/benchmark-design.md), [tools and ablations](docs/tools-and-ablations.md), and [training status](docs/training-status.md).

The environment was created with `prime env init fhir-workflows --multi-file`. It includes a source-preserving compiler, authored longitudinal episodes, an artificial infrastructure pilot, isolated CRUD operations, deterministic reference traces, and evidence/state verification. Optional helpers find patients, inspect FHIR fields, decode documents, build timeline indexes, and prepare updates. Raw and assisted profiles support matched tool ablations; a literal no-tools supplied-chart control supports three read-only families.

```bash
UV_CACHE_DIR=/tmp/fhir-uv-cache uv venv .venv --python 3.12
UV_CACHE_DIR=/tmp/fhir-uv-cache uv pip install --python .venv/bin/python -e ./environments/fhir_workflows pytest
.venv/bin/python -m pytest tests -q
.venv/bin/fhir-workflows validate environments/fhir_workflows/fhir_workflows/pilot --split dev
```

The tested local interpreter is Python 3.14.6; the package requires Python >=3.12. Dependencies are declared in pyproject.toml and the tested development dependencies are pinned in requirements-dev.lock.

To reproduce the current source-based corpus:

```bash
git clone https://github.com/sparkcpark/synthetic_hospital.git sources/hospital
git -C sources/hospital checkout 911f34c4ac65a508543c4b3b90c373a0cd16534d
.venv/bin/fhir-workflows build --source-db sources/hospital/benchmark_v1.3.db --output data/synthetic-hospital-v0.3.0
.venv/bin/fhir-workflows validate data/synthetic-hospital-v0.3.0 --split dev --traces artifacts/dev-reference-traces-v0.3.0.jsonl
.venv/bin/python scripts/package_training_data.py --source data/synthetic-hospital-v0.3.0
```

The expanded corpus has **1,268 patients, 50 resource types, 139,109 resources excluding Provenance, and 34,433 task instances across 37 workflow families**. All 5,602 source notes and 1,268 source profiles remain preserved. Hidden source diagnosis/answer tables are excluded. Authored modules add scheduling and capacity changes, rejected specimens, corrected results, imaging referrals, medication supply/administration distinctions, uncertain immunization history, insurance eligibility, billing corrections, records release, equipment handoffs, nutrition discrepancies, allergy-history entry, and nursing observations. Each patient receives a seeded subset of age-eligible modules. These are synthetic templates requiring clinical review.

[Coverage and review cases](artifacts/benchmark-v0.3.0/README.md) provide auditable counts and sample prompts, narratives, expected answers and state changes. The local environment now has broader REST support, a lossless read-only SQL Resource projection, pre-write evidence checks, scheduling invariants and domain/role/CRUD filters. Most case-paperwork tasks provide identifiers; their retrieval depth is not inflated by the number of records they touch.

Patient-level partitions are 720 train, 80 dev, 200 public and 268 heldout. Development patients come only from upstream train. Distractor shards stay within their partition, and the environment wheel includes train/dev only.

**103 tests pass**, and all **21,716 train/dev reference workflows** pass. A clean wheel load and source-preservation audit also pass. The v0.3 verification record is in [verification-summary-v0.3.0.json](artifacts/verification-summary-v0.3.0.json). Reference workflows verify compiler/environment consistency, not trained-model performance. Original snapshots, artifacts and historical v0.2 training results remain retained.

The user previously approved public visibility for [max/fhir-workflows](https://app.primeintellect.ai/dashboard/environments/max/fhir-workflows). This local v0.3 revision has not been pushed or used for a new training run. Historical model experiments are documented in [training status](docs/training-status.md); they used earlier corpus/environment versions. HF release files under `artifacts/hf-release/` also belong to the earlier revision and are not this release. See [the environment README](environments/fhir_workflows/README.md), [the data card](docs/dataset-card.md), and [the Adaption experiment](docs/adaption-experiment.md). Independent server/full-validator conformance, conformant SQL on FHIR views, and clinical review remain pending.
