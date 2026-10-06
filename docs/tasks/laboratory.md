# Laboratory

The [v0.4.0 pipeline](../generation-pipeline.md) also tests `DiagnosticReport.result → Observation.hasMember`, component-based panels, pending collection and rejected specimens. Result values must come from the linked current report; an older same-test result cannot fill a gap in a different order.

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resolve the supplied MRN and cite supporting records; excerpts are not complete FHIR resources.

## Arrange recollection after a rejected specimen · CR

**Request:** “The patient asks why the ordered HbA1c has no result. Read the laboratory rejection and arrange recollection under the existing order.”

| Resource | Relevant retrieved information |
|---|---|
| `ServiceRequest/lab-order` | HbA1c (LOINC `4548-4`); `status: active` |
| `Specimen/rejected-draw` | `status: unsatisfactory`; `request` links the same order; rejection note |
| `Communication/lab-message` | Laboratory requests a replacement specimen under the existing active order |

**Chart change:** Create one requested Task linked to the patient and existing ServiceRequest: “Arrange replacement specimen after laboratory rejection.” Preserve the order; do not manufacture a result or substitute an unrelated old measurement.

**Expected answer:** `{"specimen_status": "unsatisfactory", "result_available": false, "recollection_requested": true}`.

<!-- Source: dev task 02d29d14b2797ff0a9dc5bb2; family specimen_recollection_request. -->
