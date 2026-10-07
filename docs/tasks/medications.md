# Medications

[← Benchmark overview](../../README.md)

Shortened synthetic dev examples. Resolve the supplied MRN and cite supporting records; resource labels are abbreviated.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Distinguish dispensing from administration · R

### Request

> A relative assumes the prescription was taken because the pharmacy dispensed it. Compare the prescription, handover and observed dose record.

### Retrieval path

```text
MRN ---> Patient ---> MedicationRequest ---> Medication
                            |
                 +----------+----------+
                 v                     v
       MedicationDispense    MedicationAdministration
       handover completed    observed dose not given
                 |                     |
                 +----------+----------+
                            v
                  report both separately
                  home adherence remains unknown
```

### Retrieved evidence

- `Medication/medicine` — Acetaminophen oral preparation
- `MedicationRequest/prescription` — Active prescription for that medication/patient
- `MedicationDispense/handover` — `status: completed`; handed over on `2023-06-09`
- `MedicationAdministration/dose` — `status: not-done`; linked prescription; reason: patient declined this observed dose

<!-- Source: dev task ff1a9b785d829645bb322bf0; family medication_supply_vs_administration. -->

### Expected answer

```json
{
  "dispensed": true,
  "observed_dose_given": false,
  "reason": "Patient declined this observed dose."
}
```

No chart changes; home adherence remains unknown.

## Route an incomplete refill request · CR

### Request

> The caller wants more medication but cannot confirm current instructions. Create a review request linked to the call.

### Retrieval path

```text
MRN ---> Patient
          |
          +-----------------------+
          v                       v
   Communication           MedicationRequest
   incomplete refill call  current instructions
          |                       |
          +-----------+-----------+
                      v
              create review Task
              focus = call; preserve prescription
```

**Why these records:** The Communication documenting the call and the current MedicationRequest. The call explicitly says no renewal has been authorized.

### Chart action

Create one requested Task for the patient, focused on the Communication, asking the prescribing team to verify instructions before renewal. Leave the prescription unchanged.

<!-- Source: dev task 4658f166acb60374f43184f1; family refill_request_routing. -->

### Expected answer

```json
{
  "review_requested": true,
  "prescription_changed": false
}
```
