# Laboratory

[← Benchmark overview](../../README.md)

The [v0.4.0 pipeline](../generation-pipeline.md) also tests `DiagnosticReport.result → Observation.hasMember`, component-based panels, pending collection and rejected specimens. Result values must come from the linked current report; an older same-test result cannot fill a gap in a different order.

A shortened synthetic dev example. Resolve the supplied MRN and cite supporting records; excerpts are not complete FHIR resources.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Arrange recollection after a rejected specimen · CR

### Request

> The patient asks why the ordered HbA1c has no result. Read the laboratory rejection and arrange recollection under the existing order.

### Retrieval path

```text
MRN ---> Patient ---> ServiceRequest (existing HbA1c order)
                            |
                 +----------+----------+
                 v                     v
              Specimen            Communication
              rejected            lab requests recollection
                 |                     |
                 +----------+----------+
                            v
                  create recollection Task
                  focus = existing order
```

### Retrieved evidence

- `ServiceRequest/lab-order` — HbA1c (LOINC `4548-4`); `status: active`
- `Specimen/rejected-draw` — `status: unsatisfactory`; `request` links the same order; rejection note
- `Communication/lab-message` — Laboratory requests a replacement specimen under the existing active order

### Chart action

Create one requested Task linked to the patient and existing ServiceRequest: “Arrange replacement specimen after laboratory rejection.” Preserve the order; do not manufacture a result or substitute an unrelated old measurement.

<!-- Source: dev task 02d29d14b2797ff0a9dc5bb2; family specimen_recollection_request. -->

### Expected answer

```json
{
  "specimen_status": "unsatisfactory",
  "result_available": false,
  "recollection_requested": true
}
```
