# Implementation status — October 2, 2026

The published environment is v0.2.5, using the unchanged v0.2.1 corpus: 1,268 patients, 33 resource types, 105,066 resources excluding Provenance and 18,386 instances across 15 task families. Source notes and profiles remain preserved. [Five optional helpers and tool ablations](tools-and-ablations.md) support matched raw/assisted comparisons; the supplied-chart no-tools control covers three read-only families.

Completed free Laguna raw/assisted pilots each stayed at 2/12 on baseline, step-six and step-twelve dev evaluations. Llama failed to establish tool-call competence. Version 0.2.4 adds separately logged plain-JSON and format-tolerant workflow success, explicit demonstrations, a validated JSON-action transport, optional evidence-gated training shaping, and hosted evidence-latency/query-structure counters. The revised free campaign uses larger dev evaluations and a one-hop Llama curriculum. [Training status](training-status.md) records actual results, run receipts, experiment limits and adapter readiness; the JSON-action Llama curriculum completed with 0/16 → 7/16 → 16/16 dev workflow success and no evaluation shaping. This is one task template, not general CRUD or heldout performance. V0.2.5 corrects prompt demonstration counting in evidence-round metrics; scores remain unchanged.

Current checks: 54 tests pass, including complete mock rollouts through both protocols; all 1,160 dev reference workflows pass the revised scorer, and both protocols load from the clean extracted wheel. All 10,440 train reference workflows passed earlier unchanged-corpus checks and were not rerun for this revision. Exact hashes are in [verification](../artifacts/verification-summary-v0.2.5.json). Clinical review and independent FHIR-server/full-validator validation remain unverified. A bounded four-step GPT-OSS pilot started after useful free-model feedback; HF uploads remain deferred.

The earlier v0.1 implementation record follows for historical context.

---

# Implementation status — October 2, 2026

This is a researched benchmark design and runnable v0.1 prototype. It is ready for local model-baseline experiments, with clinical review and independent FHIR interoperability verification still pending.

## Delivered

- [Benchmark design](benchmark-design.md): clinically motivated CRUD families, known chart omissions, partial user information, dependency graphs, curriculum, evidence/state verification, efficiency metrics, and a controlled REST/hybrid comparison.
- [Dataset card](dataset-card.md): 1,268 Synthetic Hospital patients, all 5,602 source notes and 1,268 source profiles preserved, 26 resource types, and 55,917 resources excluding Provenance. There are 15,850 instances across 13 workflow families, not 15,850 independent workflow designs.
- [Environment](../environments/fhir_workflows/README.md): initialized with the Prime CLI, packaged for Verifiers 0.2.0 through the legacy environment interface, with isolated CRUD episodes, atomic transactions, evidence-aware rewards, and rollout metrics. Training and development data are bundled; public and heldout charts are excluded from the wheel.
- [Adaption experiment](adaption-experiment.md): Invent and Improve Data Quality each ran on 12 fictional proposals, using 3 displayed credits in total. No rows passed the requested schema and semantic checks, so none entered the benchmark.
- Local Hugging Face release layout in `artifacts/hf-release/`, a built environment wheel in `artifacts/wheels/`, and local/dedicated PrimeRL configuration recipes in `configs/`. No model training or external publication was performed.

| Split | Patients | Tasks | Reference replay |
|---|---:|---:|---|
| Train | 720 | 9,000 | 9,000 passed |
| Dev | 80 | 1,000 | 1,000 passed |
| Public | 200 | 2,500 | Reserved |
| Heldout | 268 | 3,350 | Reserved |

The 80 development patients come only from the upstream training partition. Distractor charts stay within their split.

## Verification and limits

All 31 tests passed, Ruff passed, the wheel built successfully, and the extracted wheel loaded through `verifiers.load_environment` from a clean directory. Its full dataset sizes, artificial pilot replay, hybrid SQL read, and rejection of an unbounded allocation query were checked. Exact counts, trace hashes and wheel hash are in [the verification summary](../artifacts/verification-summary.json).

Reference success verifies our specified environment and scorer; it is not agent accuracy. Generated resources pass the official R4 JSON schema and local reference/patient checks, not every FHIRPath invariant, terminology binding, profile, or clinical rule. The REST server implements a documented subset. The SQLite backend is experimental and does not implement conformant SQL on FHIR ViewDefinitions.

Most scored targets are deliberately constructed workflow fixtures; source notes provide chart context, and two families use explicit imported profile facts. Longitudinal topology and clinical diversity need expansion beyond these templates. A true one-to-four-hop curriculum uses dependency depth separately from tool-call count, because includes, chains and SQL joins can pack dependencies into fewer requests.

The next work is a measured local model baseline, independent FHIR-server/full-validator replay, clinically reviewed chart expansion, and an actual SQL on FHIR engine. [Next experiments](next-experiments.md) specifies those gates, SFT/RL progression, checkpoint analysis, and release requirements. Hosted GPU dispatch and eligibility remain unverified, and an explicit license for new code/generated data must be selected before publication.
