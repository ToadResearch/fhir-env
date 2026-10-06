# Referrals

[← Benchmark overview](../../README.md)

Shortened synthetic dev discovery examples (`discovery_variants=true`). The request supplies MRN and order context; secondary resource IDs must be discovered. Cite the resolved patient and supporting records.

## Check whether the referral can close · R in this case

**Request:** “Find the wrist-radiograph report, study, tracking Task and specialist reply. Complete follow-up only if referring-clinician acknowledgement is documented.”

**Retrieve:** `Patient → ServiceRequest → DiagnosticReport → ImagingStudy`, plus the Task linked to the order and the specialist-reply DocumentReference.

| Retrieved evidence | What it establishes |
|---|---|
| Final report and study | Recorded conclusion; modality `DX`; study `available` |
| Tracking Task | `status: in-progress`; `focus` links the same order |
| Decoded specialist reply | Report delivered; clinician acknowledgement **not yet recorded** |

**Expected result:** Return `acknowledged: false`, `tracking_status: in-progress`, modality/study status and the recorded report conclusion. Leave the Task unchanged. Delivery and image availability do not establish acknowledgement.

<!-- Source: dev task fad795d6b7e78336ac7138ef; family referral_closure_discovery. -->

## Log an acknowledged referral and finish tracking · CRU

**Request:** “Reconcile the documented acknowledgement: add the communication log and complete the tracking Task together.”

**Retrieve:** The same linked resource types. Here the specialist reply explicitly records referring-clinician acknowledgement and arranged follow-up; the Task is still in progress.

**Chart change:** Atomically create a completed Communication about the retrieved report with the requested timestamp/text, and set only Task status to `completed` using its current ETag.

**Expected result:** `acknowledgement_logged: true`, `tracking_completed: true`, plus retrieved modality, study status and report conclusion.

<!-- Source: dev task bb146632895c0b8946f262d2; family referral_acknowledgement_discovery. -->
