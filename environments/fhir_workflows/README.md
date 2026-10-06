# FHIR Query RL

A Verifiers/PrimeRL research environment for FHIR R4 search and full CRUD over isolated synthetic EHR snapshots. RL feasibility, supported answers, correct chart changes and retrieval efficiency are the core goals. The long-term application is an EHR search agent.

Environment **v0.3.1** extends the frozen **v0.3.0 corpus**. It preserves the original CRUD mix and adds optional multi-hop discovery tasks and checkpoint metrics. Historical pilot materials are archived in `artifacts/history/2026-10-02-pilots/`; they do not define the current experiment. This revision is local, without a Hub upload or training launch.

## Install and verify

```bash
uv pip install -e ./environments/fhir_workflows
fhir-query-rl validate data/synthetic-hospital-v0.3.0 --split dev --discovery-variants
python -m pytest tests -q
```

`fhir-query-rl` and the compatibility entry point `fhir-workflows` call the same CLI. The package name and module remain `fhir-workflows` / `fhir_workflows`; the loader is `fhir_workflows.fhir_workflows.load_environment`. It uses the legacy Verifiers interface with `verifiers==0.2.2.dev6`. No credentials are needed to construct data, replay reference plans or score.

## Data and task selection

The source expansion has 1,268 patients, 50 resource types and 34,433 instances in 37 families. Patient partitions are 720 train / 80 dev / 200 public / 268 heldout. The packaged `training_data` includes only train/dev. Original notes and profiles are preserved; generated episodes have origin tags and a provenance ledger. The small `pilot=true` corpus is an artificial infrastructure fixture.

```python
from fhir_workflows.fhir_workflows import load_environment

env = load_environment(
    data_dir="data/synthetic-hospital-v0.3.0",  # omit for packaged train/dev
    split="train", eval_split="dev",
    query_mode="rest", tool_profile="raw",
    discovery_variants=True,
    max_depth=4, efficiency_weight=0.0, retrieval_weight=0.0,
)
```

Use `family`, `families`, `domains`, `roles`, `crud`, `max_depth` and `max_examples` to select cases. `crud="U"` selects tasks requiring updates; it does not remove reads or creates from a mixed workflow. Defaults retain all original CRUD families. `discovery_variants=true` appends three deterministic discovery families: referral closure, acknowledgement logging plus Task completion, and authorized draft-claim removal. They retain original mutations and add retrieved answer/evidence fields. They add 838 train / 89 dev cases, without generating resources or modifying original task files.

Each row's `info` contains task/patient identity, split, family, declared retrieval hops, difficulty bin, workflow depth, CRUD mix, required evidence count, mutation entries, transaction requirement and reference calls. Gold answers and mutation contracts remain outside agent-visible tools. Difficulty bins are 1/easy, 2/medium, 3/hard and 4+/very_hard. Declared dependencies are not proven minimum API calls.

## Tool contract

`fhir_request(method, path, body_json="", headers_json="")` supports relative GET search/read, POST create, PUT replacement, DELETE and POST empty path for atomic transaction Bundles. Search returns a FHIR searchset Bundle. Read/write responses have ETags. Updates/deletes require current If-Match; creates get server IDs. There is no upsert or PATCH. Conditional create supports idempotency. Transactions contain 1–20 primitive write entries and roll back on failure. Deletion is confined to explicitly authorized unreferenced duplicate drafts.

The default limits are 40 backend calls and 20 model turns. `GET metadata` declares supported type-specific filters, repeated AND/comma OR, system|code tokens, day-precision dates, paging, date sorting, patient.identifier chaining and selected includes. Unsupported parameters fail with OperationOutcome. General `_has`, `_revinclude`, history, SMART auth, production role permissions and cross-server queries remain unimplemented.

`tool_profile="assisted"` adds `find_patient`, `fhir_schema`, `read_document`, `chart_timeline` and `prepare_update`. Helpers use visible data and never hidden scoring truth. Underlying searches count against the same backend-call budget. Timeline indexes do not expose unreturned content; update preparation never commits. [Tool comparisons](../../docs/tools-and-ablations.md) explain accounting.

`query_mode="hybrid"` adds `sql_query(sql)` over lossless `Resource(resource_ref, resource_type, patient_ref, resource_json)` and typed SQLite projections. Queries require explicit columns, verified field lineage and at most 50 returned rows. Direct base-table joins are supported; writes, external tables, stars, CTEs and subqueries are rejected. This is experimental SQLite retrieval; SQL-on-FHIR ViewDefinition conformance is not implemented. SQL cannot replace required scoped REST absence checks in current missing-document tasks.

`tool_profile="none"` supplies the visible chart and exposes no tools for `latest_result`, `coverage_lookup` and `longitudinal_lab_trend` only. It measures answer extraction with supplied context and cannot execute CRUD or measure search efficiency.

## Verification and response formats

Final responses contain exactly `answer` and `evidence`. Strict success requires the requested answer, actual access to required evidence fields, and exactly the authorized complete chart delta. Checks include pre-write reads where required, patient/reference integrity, preserved fields, current ETags, atomicity and every committed mutation. An ID-only SQL response cannot establish a clinical value. Missing-document answers require the specified scoped search. Ordinary state/evidence tests remain part of correctness verification.

Default reward is strict success with `retrieval_weight=0` and `efficiency_weight=0`. Optional shaping is bounded to retrieval weight ≤0.2 and efficiency weight ≤0.15; it never changes the independently reported strict success metric. Query length, novelty, helper use and apparent sophistication receive no reward.

`answer_mode="json_or_fence"` optionally accepts exactly one JSON code fence containing the response. Prose plus a fence, duplicate keys and non-JSON numeric constants are rejected. Formatting and workflow correctness have separate metrics. `tool_protocol="json_action"` provides the same tools through validated JSON commands when native function calls are unavailable. It remains tool access, even though provider API tool definitions are empty. `demonstrations=true` adds fixed fictional examples that are excluded from episode evidence and turn counters. Native tools and strict JSON remain defaults.

Resources pass official R4 4.0.1 JSON schema and local graph/semantic checks. These do not establish complete terminology, FHIRPath, US Core, independent-server or clinical conformance.

## Evaluation records

Save `info`, `trajectory`, `fhir_metrics`, `fhir_trace` and `fhir_assistance` for each evaluation rollout. The rubric exposes correctness, retrieval, mutation, search-behavior, structured query and token-usage metrics. Model tokens sum actual `Response.usage` values across calls; repeated input context contributes to input totals. Missing usage is -1 with separate coverage and observed partial totals. Reasoning tokens are already included in completion totals and are not added again.

First/all evidence counters use -1 for unobserved outcomes, accompanied by censoring/applicability flags. Prompt-supplied charts have access at round zero. Helpers expose separate visible and backend resource counts. Query structure is measured only for retrieval operations, not write payloads.

[Metric definitions and offline analysis](../../docs/metrics-and-evaluation.md) specify fixed cohort checks, difficulty/CRUD/family summaries, patient-cluster intervals and real checkpoint plots. Record explicit training step, model, seed, tool profile, query mode and corpus manifest digest alongside each rollout. No learning curves are generated from reference traces.

## Reproducibility

```bash
python scripts/export_discovery_variants.py data/synthetic-hospital-v0.3.0 \
  --output artifacts/benchmark-v0.3.1 --verify
python scripts/analyze_query_experiments.py RECORDED_ROLLOUTS.jsonl \
  --output artifacts/query-evaluation --plots
```

The native PrimeRL recipe in `configs/prime-rl.toml` is retained for future work. Full trainer-schema/GPU validation and dedicated hosted eligibility remain pending. Environment dispatch, paid training, Hugging Face uploads and Hub publication are outside the current benchmark-design revision. Resolve derivative licensing and retain source notices before eventual publication.
