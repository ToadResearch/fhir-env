# FHIR Workflows

## Current local revision: v0.3.0

The expanded benchmark contains 1,268 patients, 50 resource types and 34,433 tasks in 37 families: 19,530 train, 2,186 dev, 5,428 public and 7,289 heldout. It adds clinical, nursing, clerical, scheduling, billing, immunization, records-release and home-care workflows. Each chart receives a seeded subset of age-eligible modules. Counts represent template instances, not independently authored cases. Source notes and profiles are preserved; generated episodes still need clinical review.

New environment support includes Appointment/Account patient ownership, explicit Slot status-write scope, atomic scheduling/capacity checks, pre-write evidence verification, and a lossless read-only SQL `Resource(resource_ref, resource_type, patient_ref, resource_json)` projection for all types. It remains a bounded R4 simulator and experimental SQLite backend. `domains`, `roles`, and `crud` filters support stratified evaluation; CRUD letters select tasks requiring those operations. New tasks include both supplied-identifier operational cases and order/report/study discovery. Relationship depth and search dependency depth are distinct.

This revision launches no training and has not been pushed to the Hub. The packaged payload contains train/dev only. The repository's `docs/benchmark-v0.3.0.md` describes research sources, workflow taxonomy, generated uncertainty, exact-write contracts and limitations; `artifacts/benchmark-v0.3.0/` contains review examples. The following experiment history applies to earlier versions.

## Historical reward hacking sprint: evidence and mutation integrity

This submission studies reward hacking in synthetic EHR retrieval and chart changes. The intended behavior is to retrieve the necessary documented fields, answer with supported citations, and commit only the requested changes. The training reward requires all three. We hypothesize that easier proxies such as citing a correct resource ID, returning a plausible final answer, or reaching the correct final chart state can overestimate success: an agent may cite an unread field, claim a filtered-out timeline item as evidence, or make an invalid change and subsequently restore it.

The first experiment compares raw FHIR access with five optional helpers on identical frozen tasks and charts, using strict reward and no efficiency bonus. We inspect actual rollouts for unsupported citations, evidence precision/recall, first/all evidence access, unsafe attempts, every committed mutation, and strict task success. The hypothesis is that helper access can reduce retrieval cost while also creating tempting shortcuts if a verifier credits references without field exposure. A separate supplied-chart no-tools control is restricted to read-only families. Later proxy-reward ablations must retain the strict verifier as an independent evaluation metric; none is enabled in the initial pilots. Automated adversarial tests already cover unread citations, filtered evidence, and restore-after-corrupt writes. This is a research hypothesis, not a claim of observed model reward hacking or clinical validity.

Version 0.2.2 added this experiment description for the [Prime reward hacking sprint](https://www.primeintellect.ai/blog/reward-hacking). Version 0.2.3 matches Prime's deployed `verifiers==0.2.2.dev6` after the original exact 0.2.0 dependency downgraded the hosted environment server and made its trace schema incompatible with the trainer. Task data, rewards and tool behavior are unchanged from 0.2.1. The two original Laguna attempts at 0.2.1 were stopped before usable training batches were returned; the original replacement pilots are pinned to 0.2.3.

Version 0.2.4 responds to the completed free pilots: Laguna raw and assisted both remained at 2/12 strict development successes. It keeps the original strict-format metric, adds optional acceptance of a response consisting entirely of one JSON code fence (`answer_mode="json_or_fence"`), and separates canonical workflow correctness from formatting. Prose plus a JSON fence is rejected. Duplicate JSON keys and non-JSON numeric constants are rejected. Gold charts, tasks, citation precision, field-exposure requirements and write authorization are unchanged. Version 0.2.5 corrects evidence-round counters to exclude prompt demonstration turns; the v0.2.4 runs and their task scores remain preserved.

Optional `retrieval_weight` in [0,0.2] gives bounded partial credit for exposure of required evidence fields, only with an intact mutation audit. It does not credit merely knowing IDs, guessing answers, reading the wrong patient, or unchanged state. Partial retrieval is not workflow success. Defaults remain strict reward and no shaping. Revised Laguna native-tool pilots change answer formatting and output budget without shaping; Llama's separate retrieval curriculum compares native and explicit JSON action transport with worked examples and shaping. These are experiments, not evidence of improved clinical performance.

## What is included

An isolated, deterministic FHIR R4 research simulator and a Verifiers 0.2.2.dev6 `StatefulToolEnv` loader (legacy v0 interface). Default data is the packaged Synthetic Hospital train/dev expansion when present; `pilot=true` selects the tiny artificial infrastructure dataset. `data_dir` selects a separately compiled dataset. Patient-level train/dev/public/heldout assignments are preserved, with 80 upstream train patients reserved for development.

The fifteen original task families cover source-profile conditions and home medications, latest lab result, coverage lookup, report/result traversal, message follow-up with Task creation, transactional referral cancellation and coverage transition, correction using entered-in-error, duplicate draft DELETE, present/absent documentation, fictional prior-auth evidence, ambiguous identity clarification, serial lab retrieval, and medication-reconciliation requests. Dependency depth spans 1–4. This is a deterministic task-template prototype with clinical review still pending.

## Install and verify

```bash
uv pip install -e ./environments/fhir_workflows
fhir-workflows validate environments/fhir_workflows/fhir_workflows/pilot --split dev
python -m pytest tests -q
```

All generated resources are checked against the official FHIR 4.0.1 JSON schema plus local reference and patient-integrity checks. The schema is included in the wheel. JSON schema does not verify every FHIRPath invariant, terminology binding, US Core profile, or clinical rule.

## Tool contract

`fhir_request(method, path, body_json="", headers_json="")` returns status, headers and body. GET search returns a FHIR searchset Bundle; read/write responses return ETags. Create uses server-generated IDs. PUT is full-resource replacement and requires If-Match; no upsert. POST empty path accepts a transaction Bundle with 1–20 primitive write entries. Transactions are atomic. Conditional create via If-None-Exist supports idempotency. DELETE is available only for the designated unreferenced erroneous draft. Tool calls are capped at 40 per episode, with 20 model turns by default.

Search support is declared in GET metadata and the system prompt: type-specific filters, repeated AND/comma OR, system|code tokens, day-precision dates, paging, date sort, patient.identifier chaining, selected includes. Unsupported parameters and modifiers fail with OperationOutcome. No general `_has`, reverse include, PATCH, history, SMART auth, role-based read access or cross-server queries are implemented yet. Write scope is per patient/type/explicit delete target, plus explicitly authorized Slot status changes, and is checked before mutation.

`query_mode="hybrid"` adds `sql_query(sql)` over documented SQLite projections refreshed on every query. It supports explicit SELECT columns, joins, predicates and a 50-row output cap. Writes, extra statements, outside tables and stars are rejected. This experimental projection backend is **not** an implementation of the SQL on FHIR ViewDefinition specification. REST/SQL equivalence and field-aware citation tests are included. SQL-only abstention proofs, full FHIRPath views, multi-valued flattening beyond report results, and production query planning remain pending.

Final answers are JSON with `answer` and `evidence`. Gold answers and mutation specifications are hidden from tools. Citations must identify retrieved resources and the necessary fields; ID-only SQL results do not count as reading a value. Empty-document claims require an executed scoped search. Requested changes, all unrelated fields, the entire final state and every committed mutation are checked. An unsafe committed change fails even if subsequently restored. Efficiency reward is disabled by default; after correctness training it can be enabled up to weight 0.15.

`tool_protocol="json_action"` uses the same tools, store and verifier without provider-native function calls. Each assistant action is exactly `{"tool":"fhir_request","args":{"method":"GET","path":"metadata"}}`, and observations arrive as clearly labeled user messages. JSON commands are validated against the callable schemas; unknown tools, hidden arguments, duplicate keys, Python-tag blocks, multiple objects and malformed commands execute nothing. The agent finishes with the normal answer/evidence object. API `tools=[]` in this mode does **not** make it a no-tools control: real tool actions remain available and are counted under `agent_tool_calls` and the store's operation/cost metrics. `demonstrations=true` adds two fixed fictional examples, never observed or credited in episode state. Provider-native mode remains the default.

## Evaluation and training

For local inference, run after a model endpoint is available:

```bash
prime --plain eval run fhir-workflows --env-dir-path ./environments \
  --provider local --api-base-url http://localhost:8000/v1 --api-key-var LOCAL_MODEL_KEY \
  --model YOUR_SERVED_MODEL --num-examples 20 --rollouts-per-example 1 \
  --env-args '{"pilot": true, "query_mode": "rest"}' \
  --state-columns fhir_metrics,fhir_trace --save-results --skip-upload
```

The loader exposes separate dataset/eval_dataset objects; local evaluation should select the evaluation dataset through Verifiers. Development/reference verification uses train/dev only. Save fhir_metrics and fhir_trace to retain first-evidence and all-evidence calls/rounds, censor flags, strict success, bytes, operations, errors and query structure. Add real inference token/latency accounting from the rollout framework when running a model.

Hosted rubric metrics now include first/all evidence call and round counters with `_observed` suffixes, applicability/censor flags, and query character/parameter/predicate/chain/include/join mean and maximum values. A counter of -1 means the event was not observed: exclude it from conditional latency means and report censoring separately. Supplied context has latency zero. Query complexity is descriptive and never rewarded. Final JSON formatting is measured separately from `workflow_success`; the original `strict_success` remains available in every run.

The native PrimeRL recipe lives in `configs/prime-rl.toml`; the dedicated hosted template is `configs/hosted-dedicated.template.toml`. Current Verifiers v1 Env config supports legacy environments through nested `env.id` and `env.args`; we use that bridge. Current PrimeRL source-table format is `[[orchestrator.train.source]]`, not the removed flat env table. Full GPU dispatch/schema validation is pending, so these are recipes, not validated paid training launches.

Suggested curriculum: train depth<=2 first with no efficiency penalty, then depth<=3, then all depths with correctness retained and optional small efficiency penalty. Evaluate fixed dev cases at step 0 and each checkpoint; use public/heldout only after frozen choices. Compare REST and hybrid with identical tasks, field/response budgets and snapshots. Report success-at-budget along with first/all evidence and successful completion costs; do not fabricate training curves.

## Eventual publishing

The package already contains full train/dev data and the small pilot; public and heldout shards are excluded from the environment wheel. Rebuild this payload with `python scripts/package_training_data.py` after recompiling the source corpus.

```bash
# Publishes externally: run only when the release is approved.
prime --plain env push fhir-workflows --path ./environments --runtime v0 --visibility PUBLIC
```

`--runtime v0` is required: the current Prime CLI otherwise infers v1 from the Verifiers dependency even though this package has a legacy loader. Use the resulting namespace/version in the hosted template. Shared LoRA hosted service is transitioning on October 5, 2026; verify dedicated eligibility, cached models and GPU availability with current CLI before dispatch.

HF release files are prepared under `artifacts/hf-release/` by `scripts/prepare_hf_release.py`; no upload has been performed. Preserve the source notices and decide an explicit license for the new generated data/code before HF publication. The original free Laguna pilots completed without a dev gain. The revised Llama JSON-action one-hop pilot completed with logged dev success 0/16, 7/16 and 16/16 at baseline, step four and step eight, with no evaluation shaping; native-tool training still stalled. This is a small curriculum result, not general EHR capability. Training tokens or run completion alone do not establish learning. Run links and measured results are maintained in the workspace's docs/training-status.md.

## Required environment variables

None for environment construction, compilation, reference validation or scoring. Model inference, HF uploads and Prime dispatch require the appropriate provider credentials at run time. Never put credentials in task prompts, data or source control.

## Optional tools and no-tools control

Use `tool_profile="raw"` for native FHIR access, `"assisted"` for five optional helpers, or `"none"` for a read-only supplied-chart control. Helpers find patients, inspect field schemas, decode documents, build a bounded timeline index, and prepare updates without committing them. Underlying reads and field exposure remain audited. See the repository docs/tools-and-ablations.md for matched comparisons, accounting and limits.

The historical v0.2.1 corpus contains 18,386 task instances, including 10,440 training and 1,160 development tasks, across 1,268 patients and 33 resource types. Excluding Provenance, it has 105,066 resources. Source-aware authored follow-up narratives and linked resources remain synthetic templates requiring clinical review. The user approved public visibility to enable the requested free-tier training pilots. Only train/dev charts are packaged; public-test and heldout charts are excluded.
