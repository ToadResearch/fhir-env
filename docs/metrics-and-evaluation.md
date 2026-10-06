# Metrics and checkpoint evaluation

The benchmark tests whether RL improves FHIR querying and complete CRUD workflows. Search efficiency is meaningful only alongside correct answers, supported evidence and correct chart changes. The long-term EHR agent goal motivates efficient discovery with partial starting information.

The behavior metrics are inspired by [Engram's Harvey study](https://engram.com/blog/legal-agents-with-memory): how an agent begins searching, filter novelty, result volume and time to relevant evidence. Our definitions operate on FHIR resources and fields and differ from that study's legal matter metrics. This environment does not implement its persistent memory mechanism, and its reported results are not results for this benchmark.

## Fixed task difficulty

| Annotation | Definition |
|---|---|
| `retrieval_hops` | Longest path in the task's declared information-dependency graph |
| `difficulty_bin` | 1: easy; 2: medium; 3: hard; 4+: very_hard |
| `workflow_depth` | Retrieval depth plus a dependent mutation stage when required |
| `required_evidence_resources` | Number of gold evidence resources, including identity evidence where required |
| `required_mutation_entries` | Create/delete resource entries plus specified update-field entries; not primitive API operation count |
| `crud` | Required operations: R, CR, RU, CRU or RD |
| `requires_transaction` | Whether the case requires an atomic write group |
| `reference_calls` | Cost of the supplied valid strategy, not an optimal-cost oracle |

A supplied business identifier can make a rich multi-resource workflow one-level discovery. Conversely, a four-level dependency may be retrieved with fewer model turns using chaining/includes/joins. Hop bins are fixed before evaluation, independent of model success or rollout length. They are a structural difficulty proxy, not a validated clinical difficulty scale. Stratify additionally by family, domain, evidence count, write work and missing-information branch.

## Correctness and cost

| Measure | Interpretation |
|---|---|
| `strict_success` | Correct strict JSON answer, required field access and authorized complete state/mutation history |
| `workflow_success` | The same task obligations under the configured answer-format policy; retain strict success separately |
| Answer/state success; evidence precision/recall | Decompose failure rather than equating plausible answers with task completion |
| `model_turns` | Recorded model calls in the rollout trajectory, including the final answer; excludes fixed prompt demonstrations |
| `model_input_tokens` / `model_output_tokens` | Actual API usage summed over all recorded model calls |
| `model_total_tokens` | Input plus output; reasoning is already a subset of completion tokens |
| `token_usage_complete` / call coverage | Whether every call has valid usage; incomplete totals are -1, with observed partial totals recorded separately |
| First/all evidence calls and rounds | Access to required fields; all-evidence completion also includes required absence searches |
| Censor/applicability flags | Unobserved evidence latency is -1, rather than zero; report failure/censor rates beside conditional means |
| Backend/tool/primitive counts | Backend requests, model-dispatched tool invocations and primitive operations are distinct |
| Returned bytes and helper bytes | Backend transfer versus helper-visible output; summing these double-counts some content |

Input usage includes repeated context in later requests; it is not unique chart tokens. Missing token usage is never estimated from characters or resource bytes. A no-tools supplied-chart control has evidence access at round zero but still incurs recorded model tokens. It only supports its three read-only families.

## Search behavior

`first_action_targeted_search` means the first tool action performs a search containing at least one non-control filter value. Identity and patient filters qualify. This is an operational filtered-search label; it does not certify high selectivity, clinical relevance or success. `first_action_broad_search` captures an unfiltered type read, including `GET Observation`, or SQL without filter values. `first_action_document_read` captures direct DocumentReference reads or the decoding helper. Schema/update-preparation actions are not opening searches. `first_turn_targeted_search` allows a filtered search among multiple tool calls in the first active model round.

First-search and first-turn gold resource hits are distinct from their required-field hits. A first turn can contain several retrieval calls. An ID-only result can expose the correct reference without exposing any required value. First/all evidence latency uses field access rather than reference guessing. `unique_resources_per_search_mean` counts distinct visible references within each query, then averages across queries; duplicates across searches are retained as repeated work. Backend counts include records filtered out by helper presentation, and remain separate from visible counts. The current search summary includes attempted REST searches and SQL SELECTs, including errors with zero returned resources; report `tool_errors` alongside volume. It does not count static schema reads as searches.

`distinct_filter_terms` counts case-folded lexical tokens in REST predicate values or SQL WHERE literals. REST token system prefixes and control parameters are excluded; SQL JSON-path literals are excluded. `novel_filter_terms` counts those absent from the task request, initial visible prompt and earlier visible tool responses. This lexical measure includes dates, identifiers and status words; it is not clinical concept discovery. Helper-private backend content does not enter the visible vocabulary. Older traces without observation accounting report novelty -1. `repeated_queries` counts exact duplicate retrieval request strings, rather than semantic equivalence.

Query characters/bytes, predicates, parameters, chain depth, includes and SQL joins describe retrieval syntax. Longer queries may be inefficient or invalid. These metrics never directly affect reward.

## Frozen checkpoint protocol

Evaluate a fixed dev cohort at step zero and the same training checkpoints. Freeze task IDs, chart manifest hash, rollout count per task, hop/CRUD annotations, model/output/backend budgets, response format and evaluator before drawing curves. Compare raw/assisted and REST/hybrid on identical task IDs and chart snapshots. Keep the supplied-chart read-only control separate. Preserve patient separation and defer public/heldout tuning.

Record one JSON object per rollout with this structure (illustrative schema, not measured data):

```json
{
  "training_step": 0,
  "model": "MODEL_ID",
  "seed": 17,
  "tool_profile": "raw",
  "query_mode": "rest",
  "benchmark_manifest_sha256": "64_lowercase_hex_characters",
  "rollout_id": 0,
  "evaluation_config": {
    "environment_version": "0.3.1",
    "max_calls": 40,
    "max_turns": 20,
    "max_completion_tokens": 2048,
    "tool_protocol": "native",
    "answer_mode": "strict",
    "efficiency_weight": 0,
    "retrieval_weight": 0,
    "discovery_variants": true
  },
  "info": {
    "task_id": "FROZEN_TASK_ID",
    "patient_id": "SYNTHETIC_PATIENT_ID",
    "split": "dev",
    "family": "TASK_FAMILY",
    "retrieval_hops": 4,
    "crud": "CRU"
  },
  "metrics": {"strict_success": 0},
  "trajectory": [],
  "fhir_trace": []
}
```

Use real `info`, `fhir_metrics`/`metrics`, `trajectory` and `fhir_trace` from saved Verifiers state. Add explicit run metadata and `evaluation_config`; do not infer training steps from row order. Omit `trajectory` when unavailable rather than replacing a real trajectory with an empty list. Keep full loader arguments in the evaluation run's accompanying config as well. The corpus digest is SHA-256 of the frozen source `manifest.json`; the separate discovery manifest records variant task hashes. Distinct `rollout_id` values are required for repeated samples. Seed identifies the matched experimental/evaluation replicate.

```bash
uv pip install -e "./environments/fhir_query_rl[analysis]"
python scripts/analyze_query_experiments.py evaluations/checkpoints.jsonl \
  --output artifacts/query-evaluation --plots
```

The analyzer rejects duplicate rollouts, changed task dimensions, changing checkpoint cohorts/counts and unpaired raw/assisted/SQL cohorts. It also rejects changed evaluation budgets, response interfaces or verifier settings within a comparison. It accepts dev/public/heldout evaluation records, not training examples. It exports stable same-task query examples (three task IDs per hop/CRUD stratum, selected by sorted ID) across checkpoints and access profiles, including failed rollouts. Missing traces are flagged rather than reconstructed. It writes JSON/CSV summaries by difficulty, difficulty × CRUD and family. Means/medians are task/rollout-weighted; 95% bootstrap intervals resample patients with all their observed values. Macro family summaries and inference across multiple training seeds remain separate analyses.

Plots use colors for easy/medium/hard/very_hard across training steps. They show strict success, all-rollout model turns, successful-rollout output tokens, and observed turns to all evidence, with separate panels/files for CRUD mixes. Missing values create gaps. JSON/CSV contains both all-rollout and successful-rollout costs, token-usage coverage and censor rates, which must accompany plots. Reduced costs caused by earlier failure or dropping unsuccessful searches are not efficiency improvements. No curves are produced from deterministic reference replay.

## Search-budget scaling hypothesis

The proposed result—“models learn to scale their search budget roughly linearly with question difficulty”—is a hypothesis. Neither reference plans nor existing small pilot results establish it. Test checkpoint × difficulty interactions on frozen cases while preserving success, and examine successful-rollout cost as well as success-at-budget.

A later scaling analysis should compare families/CRUD mixes and control for required evidence, prompt/answer length, output cap and write coordination. Use multiple seeds, patient-cluster uncertainty and fixed same-task query examples. Compare linear and alternative budget relationships rather than asserting linearity from four colored means. Increased budget on harder cases can coexist with reduced waste within each difficulty. Clinical reasoning, factual recall, formatting and retrieval learning can also change costs; stronger attribution requires those controls.

Representative query examples should use preselected task IDs at every checkpoint, including failures. They should show actual queries and their evidence outcomes beside the cost curves. Query sophistication and filter novelty are descriptive supporting evidence, not standalone demonstrations of learning.
