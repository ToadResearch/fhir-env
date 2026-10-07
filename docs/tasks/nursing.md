# Nursing

[← Benchmark overview](../../README.md)

Shortened synthetic dev examples. Resolve the supplied MRN and cite retrieved evidence; excerpts below are not complete FHIR resources.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Record measured weight and close the visit · CRU

### Request

> Transcribe the signed worksheet's measured weight and close the listed visit at its documented completion time. Commit both together.

### Retrieval path

```text
MRN ---> Patient
          |
          +-----------------------+
          v                       v
    Signed worksheet       Encounter + current ETag
    weight + time          visit + completion time
          |                       |
          +-----------+-----------+
                      v
             ONE TRANSACTION
             create Observation + finish Encounter
```

### Retrieved evidence

- `DocumentReference/worksheet` — Weight **10.7 kg** at `2020-07-14T09:15:00Z`; visit completed at `09:30:00Z`
- `Encounter/visit` — `status: in-progress`; `period.start: 2020-07-14T09:00:00Z`; current version

### Chart action

In one transaction, create a final body-weight Observation linked to the patient and Encounter, using the worksheet's value/unit/time. Set Encounter status to `finished` and its end to `09:30:00Z`. Preserve earlier observations and unrelated fields.

<!-- Source: dev task 89286349593b54e7672b9ccd; family nursing_observation_and_visit_close. -->

### Expected answer

```json
{
  "weight_kg": 10.7,
  "encounter_status": "finished"
}
```

## Preserve uncertainty in reported allergy history · CR

### Request

> Record the newly reported allergy history as unconfirmed. The antibiotic and date are unknown.

### Retrieval path

```text
MRN ---> Patient ---> Communication (reported history)
                            |
                            v
                  unnamed antibiotic + uncertain date
                  reported rash + no confirmation
                            |
                            v
                  create AllergyIntolerance
                  preserve uncertainty
```

**Why these records:** The patient's Communication reports a past itchy rash after an unnamed antibiotic, no current reaction, and no clinical confirmation.

### Chart action

Create AllergyIntolerance with `code.text: Antibiotic, name unknown` and `verificationStatus: unconfirmed`, retaining the reported uncertainty. Do not invent a named drug, date or anaphylaxis.

<!-- Source: dev task 1bfcc093a53ebaeda81bc031; family reported_allergy_with_uncertainty. -->

### Expected answer

```json
{
  "recorded": true,
  "verification_status": "unconfirmed"
}
```
