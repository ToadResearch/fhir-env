# Tool assistance and controls

The `tool_profile` loader argument has three settings. `raw` exposes the existing FHIR request tool (and optional experimental SQL). `assisted` adds five deterministic helpers to that same interface. Task IDs, charts, gold answers and mutation requirements are identical between raw and assisted when the same filters are used. System instructions differ only to describe the available helpers.

| Helper | Purpose | Boundaries |
|---|---|---|
| `find_patient` | Search supplied MRN/name/date of birth and label unique, ambiguous or missing matches | Returns candidates; does not choose an ambiguous patient or write |
| `fhir_schema` | Inspect R4 top-level JSON field definitions and supported server searches | Static documentation; no task answers or clinical recommendations |
| `read_document` | Decode inline text/JSON attachments with character paging | Visible DocumentReference only; no outside URL fetch or OCR |
| `chart_timeline` | Collect dated patient resource references across up to six types | One bounded search page per type, explicit continuation; read content separately |
| `prepare_update` | Preserve current fields and ETag while constructing a proposed replacement/diff | Shape validation only; never commits or checks against hidden intended changes |

Helpers cannot access the task index, gold, latent missingness ledger or scoring function. Their injected state contains only the visible chart and ordinary authorization/audit bookkeeping. An index entry is not evidence of an unreturned lab value: field-level exposure checks cover helper results as well as SQL results. Patient reports remain reports; neither helpers nor tasks authorize diagnosis inference or unrequested prescribing.

Underlying FHIR requests count toward the same 40-request episode budget. The trace records `via_tool`, backend requests, primitive operations, bytes and sequential model rounds. `agent_tool_calls` counts dispatched model tool invocations, `helper_calls` counts completed helper results, and `helper_response_bytes` counts those results separately from backend retrieval bytes. A helper can reduce model interaction without reducing underlying chart work; both costs must be reported. Real model input/output tokens and wall time come from the rollout/training platform.

`none` is a literal no-tools control for `latest_result`, `coverage_lookup` and `longitudinal_lab_trend`. It supplies the visible patient chart in the prompt, excludes hidden omitted records and gives no tools. Those supplied resources count as available evidence at round/call zero; `context_bytes` records the chart size. This measures answer extraction given context. It cannot measure search efficiency or perform CRUD. Compare it with raw/assisted restricted to the same three families; broader CRUD experiments compare raw against assisted.

Freeze identical train/dev task IDs and chart hashes for raw/assisted and REST/hybrid comparisons. Retain all CRUD operations, with matched backend/model/output budgets and response formats. Stratify by dependency depth, workflow depth, CRUD mix, family, domain, required evidence count and missing-information branch. The new optional discovery families use the same charts and original write contracts while withholding secondary identifiers. The supplied-chart control stays restricted to matched read-only tasks.

The environment records actual model usage when available and separates backend queries from model turns and helper calls. Search behavior includes filtered versus unfiltered opening searches, first-search gold ID hits versus required-field access, visible/backend unique records per search, repeated queries and lexical filter novelty. A filtered search can still be broad within a patient; this operational label does not certify relevance. See [metric definitions and the frozen checkpoint protocol](metrics-and-evaluation.md).

Inspect strict success by family, exact state changes, evidence recall/precision, malformed answers, invalid queries, wrong-patient attempts and all-zero advantage groups before reward changes. Keep any subsequent shaped reward version separate from the strict scoring metric and preserve the same frozen dev cases. Do not reward query length, helper use, or unverified clinical conclusions.
