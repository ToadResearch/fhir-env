# FHIR Query RL benchmark design

The immediate question is whether reinforcement learning improves an agent's ability to query FHIR and complete requested chart changes. Full create/read/update/delete is retained. The longer-term application is a general EHR search agent working over private local records.

## Existing foundation

The frozen v0.3.0 Synthetic Hospital expansion has 1,268 patients, 50 resource types and 34,433 task instances in 37 families. Original profiles and 5,602 notes remain intact. Generated episodes add clinical, nursing, registration, scheduling, medication, diagnostic, insurance, billing, records and home-care workflows. [The corpus design](benchmark-v0.3.0.md) describes generation, uncertainty, provenance and resource links.

We extend this foundation instead of replacing it. Environment v0.3.1 adds optional discovery cases, difficulty annotations, recorded model-usage metrics and offline checkpoint analysis. Original gold answers, chart files, task IDs, write permissions and default CRUD mix are preserved.

## Tasks with incomplete starting information

| Request | Required discovery and decision | Chart change |
|---|---|---|
| Referral coordinator has MRN and order context; check whether follow-up can close | Resolve patient → relevant order → final report → imaging study; locate tracking Task and specialist reply; distinguish delivery from acknowledgement | Update Task status only if acknowledgement is documented; otherwise preserve it |
| Reconcile a documented specialist acknowledgement | Discover the same linked records, retrieve current Task version, and prepare the specified communication log | Create Communication and complete Task in one transaction |
| Billing staff authorize removal of a named unsubmitted duplicate claim | Resolve patient → draft claim → linked insurance Coverage → matching active Account | Delete only the authorized unreferenced draft with current ETag |

The three new discovery families append 838 train and 89 dev cases when `discovery_variants=true`. They use existing chart facts, retain original mutation contracts and request additional supported answer fields. Secondary business/resource identifiers are withheld. Case prompts still provide enough clinical or authorized business context to identify the intended episode; this revision does not introduce unresolved competing episodes requiring clarification.

Declared dependency depth is four for these variants. This describes prerequisites rather than a lower bound on API calls: patient.identifier chaining, includes, joins and alternate search paths may bypass sequential reference steps. Original supplied-ID versions remain available for comparisons, but extra answer/evidence requirements make the variants richer tasks, rather than identical prompts with IDs removed.

## Success and efficiency

The agent receives a user request and isolated chart snapshot, resolves identity, retrieves documented evidence, performs authorized operations, and supplies structured answer/evidence JSON. Strict success requires all of those obligations. Ordinary correctness verification retains patient scope, versioning, evidence access, exact state changes, preserved fields and transaction/mutation integrity.

Default reward is strict task success. Search-budget, novelty and query-complexity measures are descriptive; they do not earn reward. Metrics distinguish final-answer quality, evidence access, chart correctness, model usage and backend work. [The evaluation protocol](metrics-and-evaluation.md) defines difficulty curves, matched tool comparisons and the search-scaling hypothesis.

## Extending resource diversity

Generate coherent event sequences before resource serialization or task wording. An episode should link actors, orders, specimens, results, medication states, appointments, coverage or follow-up work with plausible chronology and explicit provenance. Preserve uncertainty and introduce distractors/omissions with known generator truth. Keep source-derived facts, generated events and omissions distinct.

Next useful extensions include multiple concurrent referral episodes, outside-record reconciliation, unresolved patient identities, temporal eligibility changes, returned communication requests and competing documentation. Each needs branch-specific answer/write contracts, reference replay, independent FHIR validation and clinical/clerical review. Synthea may provide seeded background histories, but generated resources should be integrated with authored profiles and narratives and checked against source notes before admission.

Patient holdout is already preserved. Workflow-template holdout, clinician review, full terminology/FHIRPath validation and independent server replay remain outstanding. No model training or external publication is performed by this design revision.
