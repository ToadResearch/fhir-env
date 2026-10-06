# Adaption Labs dataset pilot

The pilot requested 12 wholly fictional FHIR R4 retrieval/CRUD workflow proposals through the user's logged-in Adaption Chrome session. No source charts, real patients, upstream benchmark splits, or private patient records were uploaded. This experiment proposes scenarios; it does not establish benchmark truth or clinical/FHIR validity.

## Run and observed cost

- Mode: Invent a Dataset → Instruction dataset → Text.
- Language/localization expansion: skipped.
- Dataset ID: `0b81399f-335e-42b0-9971-80967ea4ea56`; generated name: `fhir_ehr_workflow_eval`.
- Summary offered 1K, 5K Plus, 10K Pro, and 50K Enterprise presets. The credits step allowed overriding the row count to **12**.
- Displayed balance before launch: **1,500 credits**. The 1,000-row default quoted 100 credits; the 12-row run recalculated to **2 credits**. No currency price was shown and no purchase/subscription was made.
- The agent clicked Launch only for the 12-row generator run. No AutoScientist/model-training action, interface creation, public sharing, or external publication was initiated. The generic waiting page uses the phrase “While it trains”; only the dataset generator was selected.

## Artifacts

- `artifacts/adaption/invent-prompt.txt`: exact submitted objective.
- `artifacts/adaption/summary-ui.txt`: observed proposed dataset summary, including the app-rewritten generation description.
- `artifacts/adaption/run-options.json`: selected options and displayed cost.
- `artifacts/adaption/credits-before-launch.jpg`: UI evidence of row count/cost step.
- `artifacts/adaption/invent-export.jsonl`: unmodified original download, 12 prompt/completion pairs.
- `artifacts/adaption/invent-proposals.jsonl`: parsed completion objects from the download; no semantic repairs.
- `artifacts/adaption/invent-validation.json` and `validate_export.py`: reproducible checks of the export against the original requested schema.
- `artifacts/adaption/invent-output-raw.json` and `output-view-ui.txt`: text extracted from visible UI rows; these are observations, not the authoritative export.
- `artifacts/adaption/job-success.jpg` and `output-view.jpg`: proof of completed generation.

## Prompt transformation and review requirements

Adaption rewrote the objective into a dataset description before generation. The rewrite preserved the six family names, 12 top-level completion keys, fictional-data constraints, and references to temporal/ambiguity/concurrency cases, but did not preserve all the detailed nested-field specifications or family-specific safe-write rules verbatim. This drift must be evaluated against the export, and the original prompt retained separately.

## Generation result and validation

Generation completed after approximately 11 minutes. The UI briefly returned **Not Found** when opening the enabled View link. Returning to the dataset list, reopening View and reloading recovered access. The Measure page reported 12 rows, average prompt length 111 words, completion length 313 words, quality score **9.0**, grade **A**, and percentile **43.9**. These are vendor metrics, not independent validation.

Downloaded the original JSONL through Share → Download → .jsonl without publishing or connecting an external repository. All **12 outer rows and 12 completion objects parse as JSON**, and all completions contain the requested 12 top-level keys. Two UI-rendered completions lose ETag quotation escaping and fail parsing when copied from rendered text; this is a UI extraction artifact, not a failure of the downloaded completions. The UI and downloaded row orders also differ.

No proposal meets the full requested nested schema: expected outcomes are strings/arrays rather than objects; dependency edges omit `from/to/reason` or use strings; every operation array omits at least one required `resource_type/preconditions/intended_change` field. Three rows supply facts as an object instead of an array. Scenario ID `ADAPTION-PILOT-001` repeats six times; only seven distinct scenario IDs occur.

| Workflow family | Requested | Exported |
|---|---:|---:|
| telephone_result_followup | 2 | 3 |
| coverage_transition | 2 | 2 |
| requested_order_cancellation | 2 | 0 |
| erroneous_entry_correction | 2 | 4 |
| transfer_coordination | 2 | 1 |
| fictional_prior_auth_evidence | 2 | 2 |

Semantic review found more substantial issues than JSON formatting. Examples below use one-based **export** row numbers:

- Row 1 expands a correction into an urgent referral and broad appointment cancellation, and proposes `ReferralRequest`; row 3 proposes a `TransferSummary` resource. Neither name is an R4 resource type in the [official R4 index](https://hl7.org/fhir/R4/resourcelist.html).
- Row 2 expands a coverage transition into reversal claims and eligibility requests instead of updating old Coverage and creating new Coverage. Row 10 proposes rewriting clinical orders/reports' coverage links rather than the requested coverage episode; it also uses a `pending` DiagnosticReport status. These operations require independent FHIR checks and are outside the requested pilot workflow.
- Row 6's supplied error rule calls the later null-note observation erroneous while its intended update marks the earlier one entered-in-error, a direct internal contradiction.
- Rows 7–8 add finalization/critical laboratory escalation decisions beyond a documentation retrieval workflow. Row 9 invents ethical-review evidence and corrects an insurance link instead of merely collecting evidence. The prior-auth proposals omit a concrete fictional dated checklist and include submission/validated-package language.
- Several generated prompts ask the model to create a scenario specification instead of giving the EHR agent a chart workflow request. The supplied facts frequently contain chart findings that the user request did not supply, confusing chart evidence with request facts.

The export is useful as a record of this generation experiment and as candidates for manual rewriting. **Zero rows are accepted into the benchmark**. No FHIR resource-instance, server-conformance, reference-integrity, or clinical validation was performed.

## Improve Data Quality pilot

Imported only the downloaded synthetic `invent-export.jsonl` into Improve Data Quality. Chose Instruction dataset, no localization, mapped `enhanced_prompt` and `enhanced_completion`, and retained ordinary-tier recipes **Prompt Deduplication** and **House Special**. Prompt Rephrase and Prompt Metadata Injection were off. Global constraints, reasoning traces, hallucination mitigation and checklist verification were Plus-only; no upgrade was purchased. Consequently this second pass cannot be claimed to enforce the original detailed schema or safety rules.

The pre-run evaluation again reported grade A/score 9 with percentile 43.9%, but classified the same rows as Code 75%, Medical 17%, Science 8% (the first generator's Measure view classified Medical 100%). No independent accuracy conclusion follows from these scores.

Launched a 12-row quality job, ID `83df7e4e-1d40-40b5-8bde-d73e8b533d55`, name `fhir_agent_workflow_scenarios`, for **1 displayed credit** from a balance of 1,498. The UI estimated 11 minutes; the job completed successfully. Total displayed launch costs: **3 credits**. Exact observed recipe, evaluation and cost screens are saved in `artifacts/adaption/improve-*-ui.txt` and `improve-before-launch.jpg`.

Downloaded the second output through Share → Download → .jsonl to `artifacts/adaption/improve-export.jsonl`. Saved the UI's original prompt, original completion and enhanced completion triplets separately to `improve-output-ui.json`. The improved export contains only enhanced columns, and all 12 `enhanced_prompt` values are null (Prompt Rephrase was off); all original prompts remain visible in the UI and preserved in the original export. The enhanced JSONL alone therefore lacks its paired input requests.

| Measure | Invent export | Improve export |
|---|---:|---:|
| Outer JSONL rows | 12 | 12 |
| Completions that parse as bare JSON | 12 | 2 |
| Completions satisfying requested nested schema | 0 | 0 |
| Enhanced prompt values present in export | 12 | 0 |
| Rows accepted to benchmark | 0 | 0 |
| Vendor quality score | 9.0 | 9.8 |
| Vendor grade | A | A |
| Vendor percentile | 43.9 | 57.7 |

The UI reports **+8.9% relative quality improvement**, while structured completion retention falls from 12/12 to 2/12. The Measure page's prompt/completion length labels change to 836/6,275 words; their exact counting semantics were not verified. Ten completions instead contain prose, refusals, HTTP/code walkthroughs, simulation narratives, or fenced JSON. One response expands the request into a full multi-scenario benchmark specification. The two bare JSON completions still fail the original nested schema, and one invents an out-of-range scenario ID and workflow family. These are changes in task objective/format, not demonstrated benchmark quality improvements.

The ordinary-tier quality recipe did not repair the original family balance or schema requirements. No second-pass output is accepted as benchmark truth. The useful outcome is the demonstrated need to retain exact exports and apply our own schema, family, semantics, FHIR and safety gates; vendor grades cannot replace those gates. Future generator experiments should preserve a fixed schema/rubric and inspect a small sample before expanding, but this pilot does not justify buying an upgrade.

Final validation is reproducible with `python3 artifacts/adaption/validate_export.py artifacts/adaption/invent-export.jsonl` and the same command using `improve-export.jsonl`. Results are saved in `invent-validation.json`, `improve-validation.json` and `comparison.json`. No environment code was edited, no model trained, and no dataset published externally.
