# Nursing

[← Benchmark overview](../../README.md)

Shortened synthetic dev examples. Resolve the supplied MRN and cite retrieved evidence; excerpts below are not complete FHIR resources.

## Record measured weight and close the visit · CRU

**Request:** “Transcribe the signed worksheet's measured weight and close the listed visit at its documented completion time. Commit both together.”

| Resource | Relevant retrieved information |
|---|---|
| `DocumentReference/worksheet` | Weight **10.7 kg** at `2020-07-14T09:15:00Z`; visit completed at `09:30:00Z` |
| `Encounter/visit` | `status: in-progress`; `period.start: 2020-07-14T09:00:00Z`; current version |

**Chart change:** In one transaction, create a final body-weight Observation linked to the patient and Encounter, using the worksheet's value/unit/time. Set Encounter status to `finished` and its end to `09:30:00Z`. Preserve earlier observations and unrelated fields.

**Expected answer:** `{"weight_kg": 10.7, "encounter_status": "finished"}`.

<!-- Source: dev task 89286349593b54e7672b9ccd; family nursing_observation_and_visit_close. -->

## Preserve uncertainty in reported allergy history · CR

**Request:** “Record the newly reported allergy history as unconfirmed. The antibiotic and date are unknown.”

**Retrieve:** The patient's Communication reports a past itchy rash after an unnamed antibiotic, no current reaction, and no clinical confirmation.

**Chart change:** Create AllergyIntolerance with `code.text: Antibiotic, name unknown` and `verificationStatus: unconfirmed`, retaining the reported uncertainty. Do not invent a named drug, date or anaphylaxis.

**Expected answer:** `{"recorded": true, "verification_status": "unconfirmed"}`.

<!-- Source: dev task 1bfcc093a53ebaeda81bc031; family reported_allergy_with_uncertainty. -->
