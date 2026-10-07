# Clinical search

[← Benchmark overview](../../README.md)

Shortened synthetic dev examples. Resource labels are abbreviated; answer fields below also require supporting resource citations in a full submission.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Find the latest valid lab result · R

### Request

> For this known patient, return the most recent final hemoglobin result as of June 14, 2023.

### Retrieval path

```text
Supplied patient identity
          |
          v
Observation search: hemoglobin / final / on or before cutoff
          |
          v
Compare measurement dates ---> latest qualifying Observation
                                          |
                                          v
                                    value + unit
```

**Why these records:** Patient-scoped Observations matching hemoglobin (LOINC `718-7`). Inspect status, measurement date, value and unit; an older result or an invalid result cannot substitute for the latest final one.

### Retrieved evidence

- `Observation/hemoglobin` — `status: final`; `effectiveDateTime: 2023-06-06`; `valueQuantity: 11.1 g/dL`

<!-- Source: dev task 287ed30a10ef418f7818209b; family latest_result. -->

### Expected answer

```json
{
  "value": 11.1,
  "unit": "g/dL"
}
```

No chart changes.

## Follow an imaging order to its study · R

### Request

> Using the supplied wrist-radiograph order identifier, find its final report and underlying study. Return modality, study status and report conclusion.

### Retrieval path

```text
MRN ---> Patient ---> ServiceRequest (supplied order identifier)
                            |
                            | DiagnosticReport.basedOn
                            v
                      DiagnosticReport
                       /            \
             conclusion              imagingStudy reference
                                            |
                                            v
                                      ImagingStudy
                                      modality + status
```

**Why these records:** Resolve the MRN, then follow `ServiceRequest → DiagnosticReport → ImagingStudy`.

### Retrieved evidence

- `ServiceRequest/wrist-order` — Completed wrist-radiograph order for the resolved patient
- `DiagnosticReport/wrist-report` — `status: final`; `basedOn` links the order; `imagingStudy` links the study; documented conclusion
- `ImagingStudy/wrist-study` — `modality.code: DX`; `status: available`; linked to the same order/patient

<!-- Source: dev task dae0d7da82297501cab3609c; family imaging_report_linkage. -->

### Expected answer

Modality `DX`, study status `available`, and the recorded conclusion: “No acute displaced fracture identified. Clinical follow-up recommended if symptoms persist.” No chart changes or new clinical interpretation.
