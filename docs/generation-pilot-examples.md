# Generated resource examples

[Back to the comparison](generation-pilot.md). These are shortened excerpts from inspected candidate charts. References are abbreviated; complete charts use opaque IDs. These are synthetic structural test entries, not clinical recommendations.

## One order, several linked results

For the 71-year-old example at snapshot 2023-05-14:

```text
ServiceRequest: selected lipid measurements; authored May 2
  |
  +-- Specimen: available; collected May 4 at 08:00
  |
  +-- DiagnosticReport: corrected; issued May 6
        |
        +-- Observation: selected measurements panel
              +-- Cholesterol: 5.12 mmol/L
              +-- Triglyceride: 1.61 mmol/L
```

The current panel excerpt:

```json
{
  "resourceType": "Observation",
  "status": "corrected",
  "code": {"text": "Selected lipid measurements"},
  "basedOn": [{"reference": "ServiceRequest/order"}],
  "specimen": {"reference": "Specimen/sample"},
  "effectiveDateTime": "2023-05-04T08:00:00Z",
  "issued": "2023-05-06T10:00:00Z",
  "hasMember": [
    {"reference": "Observation/cholesterol"},
    {"reference": "Observation/triglyceride"}
  ]
}
```

The same measurements can instead be emitted as components or individual report results. The expected answer stays the same. Old generated measurements primarily use individual `valueQuantity` Observations.

## A rejected collection is not an old result

The one-year-old example has a caregiver caller and a rejected current specimen. A completed older order still has a result. The agent must follow the current order's links.

```text
Older completed order --> final result

Current active order --> unsatisfactory specimen --> cancelled report
                                                        no measurements
RelatedPerson caregiver --> Communication about current order
```

The current accession's expected answer is:

```json
{
  "report_status": "cancelled",
  "specimen_status": "unsatisfactory",
  "measurements": []
}
```

An order awaiting collection instead has an unavailable specimen with no collection timestamp and a registered report with no results.

## Changes with dependencies

Cancellation changes two linked records atomically, preserving unrelated fields:

```text
Before: Appointment booked    --> Slot busy
After:  Appointment cancelled --> Slot free
```

Draft cleanup uses two Tasks linked to the same caller Communication. One is the original requested triage Task; the other is an unsubmitted duplicate draft. Only draft removal is authorized. The original Task and call must remain.

## Missing information has a structure

A caller does not know a current weight. The candidate records this rather than copying an older measured value:

```json
{
  "resourceType": "Observation",
  "status": "final",
  "code": {"text": "Patient-reported current weight"},
  "dataAbsentReason": {
    "coding": [{
      "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
      "code": "asked-unknown"
    }]
  }
}
```

There is no `valueQuantity`. Other entries exercise coded answers. QuestionnaireResponse uses a single identifier object, while ServiceRequest and most business-identified resources use arrays; the independent validator caught this distinction during the pilot.
