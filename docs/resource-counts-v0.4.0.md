# FHIR resource counts — v0.4.0

Counts are from the generated immutable corpus, including all four patient partitions. [How they were generated](generation-pipeline.md).

| Resource | Original v0.3.0 | Enhanced v0.4.0 |
|---|---:|---:|
| `Account` | 635 | 635 |
| `AllergyIntolerance` | 403 | 403 |
| `Appointment` | 2,536 | 3,804 |
| `AppointmentResponse` | 0 | 1,268 |
| `CarePlan` | 1,268 | 1,268 |
| `CareTeam` | 1,268 | 2,536 |
| `ChargeItem` | 635 | 635 |
| `Claim` | 635 | 635 |
| `Communication` | 10,174 | 11,442 |
| `CommunicationRequest` | 1,328 | 1,328 |
| `Composition` | 3,804 | 3,804 |
| `Condition` | 3,987 | 3,987 |
| `Consent` | 1,932 | 1,932 |
| `Coverage` | 2,536 | 3,804 |
| `CoverageEligibilityRequest` | 635 | 1,903 |
| `CoverageEligibilityResponse` | 635 | 1,903 |
| `DetectedIssue` | 0 | 1,268 |
| `Device` | 1,934 | 1,934 |
| `DeviceRequest` | 666 | 666 |
| `DeviceUseStatement` | 666 | 666 |
| `DiagnosticReport` | 4,995 | 7,531 |
| `DocumentManifest` | 0 | 1,268 |
| `DocumentReference` | 18,231 | 22,035 |
| `Encounter` | 11,348 | 12,616 |
| `EpisodeOfCare` | 1,268 | 2,536 |
| `Flag` | 1,268 | 1,268 |
| `Goal` | 1,268 | 1,268 |
| `HealthcareService` | 0 | 1,268 |
| `ImagingStudy` | 558 | 558 |
| `Immunization` | 611 | 611 |
| `List` | 1,268 | 2,536 |
| `Location` | 1,268 | 1,586 |
| `Medication` | 593 | 1,861 |
| `MedicationAdministration` | 593 | 593 |
| `MedicationDispense` | 1,558 | 2,826 |
| `MedicationRequest` | 1,861 | 3,129 |
| `MedicationStatement` | 3,551 | 4,819 |
| `NutritionOrder` | 666 | 666 |
| `Observation` | 17,117 | 24,651 |
| `Organization` | 3,200 | 4,154 |
| `Patient` | 1,268 | 1,268 |
| `Practitioner` | 1,268 | 1,586 |
| `PractitionerRole` | 1,268 | 1,586 |
| `Procedure` | 1,268 | 1,268 |
| `Provenance` | 136,542 | 185,512 |
| `Questionnaire` | 1,268 | 2,536 |
| `QuestionnaireResponse` | 1,268 | 2,536 |
| `RelatedPerson` | 1,268 | 1,488 |
| `Schedule` | 1,268 | 2,536 |
| `ServiceRequest` | 6,896 | 9,432 |
| `Slot` | 3,804 | 5,072 |
| `Specimen` | 5,070 | 6,338 |
| `SupplyDelivery` | 666 | 666 |
| `Task` | 5,630 | 9,434 |

Total: **374,859**, including **185,512 Provenance**. Excluding Provenance: **189,347** across **1,268 patients**.

The repository package contains train/dev only. Full generated snapshots are under `data/synthetic-hospital-v0.4.0/`, a Git-ignored directory; reproduce them with the documented CLI.
