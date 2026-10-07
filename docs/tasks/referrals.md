# Referrals

[← Benchmark overview](../../README.md)

Shortened synthetic dev discovery examples (`discovery_variants=true`). The request supplies MRN and order context; secondary resource IDs must be discovered. Cite the resolved patient and supporting records.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Check whether the referral can close · R in this case

### Request

> Find the wrist-radiograph report, study, tracking Task and specialist reply. Complete follow-up only if referring-clinician acknowledgement is documented.

### Retrieval path

```text
MRN ---> Patient ---> ServiceRequest
                            |
              +-------------+-------------+
              v                           v
       DiagnosticReport              tracking Task
       conclusion + study link       status + current version
              |                           |
              v                           |
         ImagingStudy                     |
              |                           |
              +-------------+-------------+
                            |
                 specialist reply (DocumentReference)
                            |
                            v
                 acknowledgement NOT documented
                 leave tracking Task unchanged
```

**Why these records:** `Patient → ServiceRequest → DiagnosticReport → ImagingStudy`, plus the Task linked to the order and the specialist-reply DocumentReference.

### Retrieved evidence

- Final report and study — Recorded conclusion; modality `DX`; study `available`
- Tracking Task — `status: in-progress`; `focus` links the same order
- Decoded specialist reply — Report delivered; clinician acknowledgement **not yet recorded**

<!-- Source: dev task fad795d6b7e78336ac7138ef; family referral_closure_discovery. -->

### Expected answer

Return `acknowledged: false`, `tracking_status: in-progress`, modality/study status and the recorded report conclusion. Leave the Task unchanged. Delivery and image availability do not establish acknowledgement.

## Log an acknowledged referral and finish tracking · CRU

### Request

> Reconcile the documented acknowledgement: add the communication log and complete the tracking Task together.

### Retrieval path

```text
MRN ---> Patient ---> ServiceRequest
                            |
              +-------------+-------------+
              v                           v
       report + study                tracking Task + ETag
              |                           |
              +-------------+-------------+
                            |
                 specialist reply (DocumentReference)
                 acknowledgement IS documented
                            |
                            v
                    ONE TRANSACTION
                    create Communication + complete Task
```

**Why these records:** The same linked resource types. Here the specialist reply explicitly records referring-clinician acknowledgement and arranged follow-up; the Task is still in progress.

### Chart action

Atomically create a completed Communication about the retrieved report with the requested timestamp/text, and set only Task status to `completed` using its current ETag.

<!-- Source: dev task bb146632895c0b8946f262d2; family referral_acknowledgement_discovery. -->

### Expected answer

`acknowledgement_logged: true`, `tracking_completed: true`, plus retrieved modality, study status and report conclusion.
