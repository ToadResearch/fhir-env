# Resource generation pilot

**Recommendation: keep the existing workflow breadth and adopt the pilot's structural patterns, event histories and validation.** The candidate improves selected querying scenarios; it does not replace the broader existing pipeline.

We tested 24 existing development patients, six each in child, adult 18–39, adult 40–64 and 65+ age bands. Both generators use authored Python logic and deterministic sampling. The candidate adds standards-derived patterns and explicit state transitions. No Synthea, LLM generation, training, publication or frozen-corpus changes were involved.

```text
Same existing dev patients
           |
     +-----+-------------------------+
     |                               |
Old generated chart             Explicit episode plan
Frozen control                  + pinned R4 example patterns
                                + definition-derived contracts
                                     |
                                Compile resources and versions
                                Keep history/oracles in sidecars
     |                               |
     +---------------+---------------+
                     |
          Equal budgets by resource type
          Schema + graph + official-validator checks
          CRUD/query replay + manual resource inspection
```

## Generated data and tasks

The candidate produced **820 additions across 25 resource types** and **144 tasks** in six families. Five episode domains cover laboratory coordination, pharmacy handoff, scheduling, insurance eligibility and telephone follow-up. Tasks include reading results, creating a follow-up Task, atomically cancelling an appointment and releasing its slot, and deleting an authorized duplicate draft while preserving the original triage Task.

The plan includes all 12 combinations of four lab states—final, pending, rejected and corrected—and three representations: individual Observations, a parent with `hasMember`, or `component` measurements. Each age band receives multiple states. Other variations include inline versus referenced medications, preparation versus handover, documented versus missing benefit periods, coded answers and explicit `dataAbsentReason`.

Clinical values are simple seeded fixtures. Expected laboratory answers are derived from serialized resources. Event plans, history and scoring keys are sidecars, outside the visible chart. Resource IDs do not encode branches or answers.

## Measured comparison

The old generated cohort has 2,164 resources across 43 types, including referrals, equipment, records release, immunizations and billing outside this small candidate's scope. Counts exclude Provenance and source-derived/mixed resources.

We compared **442 records per side**, with equal counts within 12 common resource types. Figures are means over 100 deterministic subsamples.

| Measure | Old | Candidate |
|---|---:|---:|
| Distinct JSON field-path shapes | 36.43 | 33 |
| Selected query-relevant features present | 21.82 | 29 |
| References excluding Patient links | 585.02 | 738 |
| Observation shapes, before subsampling | 4 | 11 |

The old pipeline retains more total shapes; the candidate adds more of the chosen query features and more linked context. Shapes exclude identities, metadata and narrative bodies. The feature checklist is defined in the script and was a design target, not an unbiased universal diversity score. Link count alone is not a quality score. These descriptive results do not establish population realism or statistical superiority.

All **144 candidate reference workflows**, **660 existing reference workflows** and **96 query probes** passed. Candidate reads passed a discovery audit: queried IDs must occur in the prompt or earlier tool responses. Recompilation and reversing resource order preserved results. These checks do not measure model performance.

## Manual inspection

I inspected seven paired excerpts across six patients, then rechecked corrections to callers, prescription entries, narratives and discovery plans. This was agent inspection, not clinician review.

| Area | Finding |
|---|---|
| Laboratory | Old already supports rejection and correction, but generated Observations are scalar. Candidate panels and components add query variation and stronger result–specimen–order relationships. |
| Corrections | Candidate corrections retain resource identity and prior versions in a sidecar. History is not exposed through the current API. |
| Missingness | Pending/rejected episodes produce no usable measurements. Unknown current weight uses `dataAbsentReason`; earlier measured weight stays separate. |
| Medications | Preparation, handover and reported use are distinct. Missing imported dose instructions are documented. Medication vocabulary remains small. |
| Clerical changes | Cancellation preserves the appointment and releases its slot atomically. Draft deletion leaves the original triage Task and call intact. |
| Realism repairs | Pediatric callers now use RelatedPerson caregivers. A two-test lipid subset is labelled “Selected lipid measurements,” rather than a full panel. |
| Narrative quality | Candidate notes agree with the structured state but are repetitive. Old modules are broader and sometimes use more source-specific context. |

See [selected resource examples](generation-pilot-examples.md). Full local paired charts are in `artifacts/generation-pilot/old/` and `new/`; `review-pack.md` contains the inspection excerpts.

## Independent validation

The [official HL7 validator](https://hl7.org/fhir/R4/validation.html), version 6.10.4, checked base R4 4.0.1 locally with remote terminology validation disabled (`-tx n/a`).

Initially, the candidate had 24 errors: QuestionnaireResponse requires one identifier object, while its general constructor supplied an array. Schema checks missed this. The constructor was fixed and a regression test added. **Final candidate inputs have zero errors.** Warnings remain, including missing resource narratives and unresolved local terminology.

Old inputs have **24 errors**: 15 DeviceUseStatement references to DeviceRequest where R4 `basedOn` expects ServiceRequest, and nine source-mapped AllergyIntolerance resources missing required clinical status. These are recorded rather than silently repaired in the frozen control.

Scopes differ: old inputs contain 2,588 chart resources; candidate inputs contain 820 additions plus the same 24 Patients. Merged candidate charts inherit old errors. Counts diagnose concrete defects, not a comparable quality rate. This is not complete terminology validation, US Core conformance, clinical validation or independent-server replay.

## What to adopt

Keep the current patients and workflow families. Adopt definition-aware cardinalities and choices, varied panel representations, explicit missingness, stronger episode relationships, version histories and independent validation. Apply them to the existing modules rather than replacing the corpus with five repeated episodes.

Next priorities are shared provider/organization graphs across patients, more varied timelines and wording, fixes for the old conformance issues in a new version, and independent-server query checks. Current API access to Observation members requires direct reads; history is not exposed.

## Reproduce

```bash
.venv/bin/python scripts/compare_generation_pilot.py
.venv/bin/python scripts/fetch_generation_patterns.py
.venv/bin/python -m pytest tests/test_generation_pilot.py -q
```

The second command reproduces the checked-in catalog from cached or downloaded sources, verifying pinned hashes. Generation works offline. Catalog contracts are extracts from 12 R4 definitions, not a full validator. Blood-pressure and lipid-result patterns are used by the generator; downloaded Appointment and Specimen examples are source references, not copied histories.

For independent validation, download `validator_cli.jar` from [release 6.10.4](https://github.com/hapifhir/org.hl7.fhir.core/releases/tag/6.10.4) into `artifacts/generation-pilot/standards/validator_cli-6.10.4.jar`, then run:

```bash
java -Duser.home="$PWD/artifacts/generation-pilot/validator-home" -Xmx3g \
  -jar artifacts/generation-pilot/standards/validator_cli-6.10.4.jar \
  artifacts/generation-pilot/validator-inputs -version 4.0.1 -tx n/a \
  -output artifacts/generation-pilot/official-validation.json
.venv/bin/python scripts/summarize_generation_validation.py
```

`summary.json` records source hashes, seed and coverage. `validation-summary.json` records validator hash, scopes and findings. Generated outputs remain ignored by Git; the compiler, catalog, scripts, tests and report are tracked.
