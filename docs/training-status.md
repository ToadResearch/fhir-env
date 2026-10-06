# Free-model training status — October 2, 2026

The completed v0.2.3 pilots did not demonstrate improved FHIR search. Laguna raw and assisted each scored **2/12** on the baseline, step-six and step-twelve dev evaluations. Llama failed to establish reliable native tool use. The published revision is now **[max/fhir-workflows@0.2.5](https://app.primeintellect.ai/dashboard/environments/max/fhir-workflows)**, with the accepted free campaign pinned to 0.2.4 and a four-step GPT-OSS pilot pinned to 0.2.5. Version 0.2.5 corrects example-turn counting without changing scores. The source-based corpus remains v0.2.1; public-test and heldout charts are excluded from the wheel.

## Completed measurements

The [14:36 UTC snapshot](../artifacts/training/snapshots/20261002T143638Z/summary.json) contains the actual status, metrics, usage, logs and available training samples. All eight v0.2.3 runs report **$0 total cost**. [Extracted dev measurements](../artifacts/training/completed-v0.2.3.csv) retain all evaluation cases, including truncated responses.

| Model / profile | Baseline | Step 6 | Step 12 | Final status |
|---|---:|---:|---:|---|
| Laguna / raw FHIR | 2/12 | 2/12 | 2/12 | Completed 12 steps |
| Laguna / assisted | 2/12 | 2/12 | 2/12 | Completed 12 steps |
| Laguna / supplied chart, no tools | 3/12 | 4/12 | — | Completed 6 steps |

Raw operation-call means were 3.92, 4.42 and 4.25 across those evaluations; assisted means were 3.83, 3.50 and 3.83. These averages include failed tasks and do not measure time to a correct answer. Neither series demonstrates an efficiency gain. The no-tools control supplies the full visible patient chart and uses only three read-only families; it is not a matched search or CRUD ablation. Its truncation rate increased from 5/12 to 6/12.

| Original run | Recorded result |
|---|---|
| [Laguna raw](https://app.primeintellect.ai/dashboard/training/lmvfs23qx6ibuyh2wafz6ray/metrics) | Completed; 174,290 training tokens |
| [Laguna assisted](https://app.primeintellect.ai/dashboard/training/bsmu4u431sjvb5jpii120x6q/metrics) | Completed; 296,636 training tokens |
| [Laguna supplied chart](https://app.primeintellect.ai/dashboard/training/z9yuz3n2jrvvlkvxyuys5sfl/metrics) | Completed; 1,730,677 training tokens |
| [Llama raw](https://app.primeintellect.ai/dashboard/training/uwt9bwa5kafdm06ws8ippm83/metrics) | Failed before usable updates; native call errors and zero-reward groups |
| [Llama assisted](https://app.primeintellect.ai/dashboard/training/uxzqjnyjorc3g9c3duxx2u32/metrics) | Stopped after zero-reward stalls |
| [Llama supplied chart](https://app.primeintellect.ai/dashboard/training/v6qdaacder5arbfdw26jz4pj/metrics) | Failed; zero rewards and substantial truncation |
| [Llama artificial warm-up](https://app.primeintellect.ai/dashboard/training/wb6oq854lriwzcoths3nw1d4/metrics) | Failed; inference rejected multiple tool calls |
| [Llama single-call warm-up](https://app.primeintellect.ai/dashboard/training/r4c3fs8d0ul567wmjz3ofju1/metrics) | Failed; 77,558 training tokens but no verified task success |

The [original manifest](../artifacts/training/pilot-runs.json) freezes run IDs, launch configs and hashes. Asynchronous orchestrator counters and billed training tokens are not evidence of useful learning. Saved training samples may contain only post-filter effective groups: do not use their success rate as an unbiased accuracy estimate. Only the pre-batch zero-advantage filter was disabled; the hosted post-batch filter still aborts after ten consecutive untrainable batches.

## Latest revised-run feedback

The [15:14 UTC campaign snapshot](../artifacts/training/campaign-snapshots/20261002T151433Z/summary.json) confirms:

| Revised run | Status at capture | Baseline | Latest dev measurement |
|---|---|---:|---:|
| Llama 1B / JSON actions | Completed 8 steps | 0/16 | 16/16 at step 8, policy version 6 |
| Llama 1B / native tools | Failed before usable updates | 2/16 | 2/16; ten consecutive untrainable batches |
| Laguna / raw | Running, step 4/12 | 26/48 | Baseline; step-four eval not yet recorded |
| Laguna / assisted | Running, step 4/12 | 22/48 | Baseline; step-four eval not yet recorded |
| GPT-OSS-20B / raw | Running, startup | Pending | Pending |

The Llama JSON-action curve was **0/16 → 7/16 → 16/16**, measured at orchestrator steps 0, 4 and 8 and policy versions 0, 3 and 6. Evaluation shaping was zero; each measurement uses 16 cases from the frozen 32-case dev pool. This is one authored hemoglobin retrieval template with fictional worked examples, not general CRUD capability, heldout accuracy or an efficiency improvement. Repeated measurements are dev tuning. All four revised free runs still report $0 cost.

![Recorded Llama JSON-action dev curve](../artifacts/training/llama-json-retrieval-learning.png)

[Independent replay](../artifacts/training/json-pilot-replay.json) reproduced all 32 saved step-seven training sample rewards: 21 plain-JSON full successes, seven partial-credit-only cases, and four failures. These counts validate explicit actions and the verifier; the saved training sample distribution is not an unbiased accuracy denominator. Successful samples use actual patient/code/status/as-of filters, descending date sort, and a one-row result, then cite the retrieved observation. Failed samples such as an exact-date filter followed by an invented observation ID remain failures.

The higher revised Laguna baselines are **not training gains**: answer acceptance, instructions, output allowance and evaluated case count changed from the earlier pilots. The matched raw/assisted runs will inform helper effects within this revised setup.

## Findings and implemented changes

Some Laguna rollouts executed the correct GET/PUT workflow and satisfied the state checks, but returned fenced JSON, prose, missing answer fields or unnecessary citations. A formatting failure should be distinguishable from retrieval failure. Version 0.2.4 adds an explicit `answer_mode="json_or_fence"`: exactly one JSON object, optionally inside one full-response JSON fence, can earn workflow reward. Prose extraction, missing fields, invented evidence and unsafe mutations remain rejected. The original plain-JSON `strict_success` is still logged alongside `canonical_success` and `workflow_success`; defaults remain strict.

Llama responses often emitted `<|python_tag|>` and schema-shaped function definitions as ordinary assistant text, with no actual tool calls. Disabling parallel calls eliminated observed baseline API errors in the single-call diagnostic, but did not produce task success. The new loader offers two explicit protocols: native function calling and validated ordinary-text JSON actions. Both execute the same bounded tools and store. JSON actions reject duplicate keys, unknown functions, hidden arguments and malformed values. This is a transport experiment, not a no-tools condition or an automatic fallback that repairs arbitrary model output.

Two fixed fictional demonstrations illustrate actual actions and final citations. They do not contain episode gold answers or count as retrieved evidence. A small Llama curriculum uses the real source-based charts and only the one-hop latest-result family. Optional training shaping gives at most 0.2 for exposing required evidence fields through an actual tool operation, gated on the correct resource and mutation integrity. It cannot turn an incomplete workflow into a success. Dev evaluation uses zero shaping; Laguna reruns use zero shaping in both training and evaluation.

Version 0.2.4 also exposes numeric first/all evidence call and round counters, observation flags, action-format errors and query structure through hosted rubric metrics. Unobserved counters use **-1**: exclude that sentinel when calculating latency and report censor rates. Condition efficiency comparisons on verified task success. Query characters, predicates, search parameters, chain depth, includes and joins are descriptive metrics, not rewards. The completed Llama deployment saves these in per-rollout training metrics but its dev aggregates expose reward and framework statistics only; checkpoint replay is still needed for a dev efficiency curve. JSON actions have native `total_tool_calls=0`; use `agent_tool_calls` and operation costs when comparing protocols. In v0.2.4, demonstration-enabled runs counted the five prompt example assistant turns in evidence-round counters. V0.2.5 subtracts those prompt turns, and both protocol regression tests require first evidence at actual round one. V0.2.4 task scores and call counts are unaffected; subtract five from its observed demonstration-enabled round values for historical analysis, preserving -1 censor sentinels.

## Revised free campaign

The [revision manifest](../artifacts/training/revision-pilot-runs.json) records accepted runs and frozen configs.

| Revised run | Protocol / access | Planned steps |
|---|---|---:|
| [fhir-v0-2-4-llama1b-json-retrieval](https://app.primeintellect.ai/dashboard/training/tc11uaw9gk1jzczv36ysweku/metrics) | json_action / raw | 8 |
| [fhir-v0-2-4-laguna-raw](https://app.primeintellect.ai/dashboard/training/ae25tah63yrjeb435j5ql1on/metrics) | native / raw | 12 |
| [fhir-v0-2-4-laguna-assisted](https://app.primeintellect.ai/dashboard/training/u17ev5dsyq12z27k6ekjws14/metrics) | native / assisted | 12 |
| [fhir-v0-2-4-llama1b-native-retrieval](https://app.primeintellect.ai/dashboard/training/lckipsidgglprsv1jhense7d/metrics) | native / raw | 8 |
 The [recipe generator](../scripts/create_revision_pilots.py) only prepares configs; the separate launcher creates one selected run and blocks retries after an uncertain creation outcome.

- **Laguna raw and assisted:** matched 192-task train pool, eight families, depth <=2, 12 turns, 2,048 generated tokens per response, 12 steps, 32 rollouts per batch and four per example. The same 48-case dev pool is evaluated at baseline and every four steps. No demonstrations, retrieval shaping or efficiency reward. This tests helper access within the revised protocol; version-to-version comparisons also change output allowance and answer formatting.
- **Llama native and JSON-action retrieval:** matched 64-task train pool, latest-result family, full source-based charts, eight turns, 2,048 output tokens, eight steps, 32 rollouts per batch and four per example. Both have demonstrations and training-only retrieval shaping. Evaluation uses 16 cases from the same 32-case dev pool, no shaping, baseline and every four steps. Parallel native calls are disabled. This diagnoses transport competence; it does not isolate shaping or demonstration effects.

Both protocols retain the original plain-JSON metric. New experiments should be compared using the same answer mode, patient split, task pool and call budget. Do not describe a change in formatting acceptance as learned clinical reasoning. The useful free JSON-action result triggered the user-requested [GPT-OSS-20B pilot](https://app.primeintellect.ai/dashboard/training/q5q0i7uhb728jt69fnme6m6x/metrics). It has four steps, 16 rollouts per batch, four per example, a 4,096-token response allowance and baseline/final 48-case dev evaluation on the same eight-family pool as Laguna. It uses native raw tools and zero retrieval/efficiency shaping. This is a paid run: launch-time prices were $0.10 input, $0.30 output and $0.40 training per million tokens. Actual usage is captured separately; a zero startup usage record is not a promise of zero final cost. [Its immutable receipt and config](../artifacts/training/gpt-oss20b-pilot-runs.json) are retained. Startup logs show baseline evaluation in progress, with one `ModelError` recorded by 15:19 UTC; no GPT-OSS task score or usable training update is established yet. The shared-run log API returned orchestrator output even when selecting the eval environment, so the underlying error detail remains unavailable in that capture. No HF dataset or model upload has been performed.

Capture status with:

```bash
python3 scripts/snapshot_free_pilots.py --manifest artifacts/training/campaign-runs.json --output artifacts/training/campaign-snapshots
```

This is a read-only snapshot command, not an automatic monitor.

## Verification and release limits

[Revision verification](../artifacts/verification-summary-v0.2.5.json) records **54 passing tests**, Ruff, clean extracted-wheel loading of both protocols, and all **1,160 dev reference workflows passing** after the scorer refactor. Tests exercise complete mock-model rollouts, invalid actions, unread/ID-only/wrong-patient evidence, and corrupt-and-restore writes. Earlier checks passed all 10,440 train reference workflows on the unchanged corpus; those were not rerun for this revision. Reference replay verifies infrastructure, not trained-model accuracy, clinical validity or independent FHIR-server conformance.

The v0.2.5 wheel SHA256 is `2dee90954c6e785512d6edd0b83af11dbe407be9d8607f7e994537e443c1d968`. Publication is confirmed in [the push log](../artifacts/training/publish-v0.2.5.log). It retains `verifiers==0.2.2.dev6`, matching the hosted legacy trace bridge. Earlier v0.2.1 traces were incompatible because that wheel downgraded the server to obsolete MessageNode fields; those initial runs were stopped before usable training batches.

The [post-completion adapter inventory](../artifacts/training/adapters-after-completion.json) showed READY step-six Laguna raw/assisted adapters and later adapters still uploading or pending. No weight download has been verified. The dashboard reports that shared LoRA launches stop accepting new runs on October 5, 2026; preserve downloadable outputs once ready and check dedicated options before subsequent campaigns. Clinical review, independent FHIR validation and a conformant SQL on FHIR engine remain pending.
