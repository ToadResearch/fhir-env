# Evidence collection

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resolve the supplied MRN and cite supporting records; this fictional checklist is not a real payer policy.

## Assemble a prior-authorization documentation checklist · R

**Request:** “As of November 14, 2023, check whether the requested specialty review, a prerequisite within 90 days, and outside-study documentation are present.”

| Resource or search | Relevant retrieved information |
|---|---|
| `Questionnaire/checklist` | Defines all three requirements and the 90-day window |
| `ServiceRequest/review` | Active specialty-review request, documented November 14 |
| `Procedure/prerequisite` | Completed prerequisite review dated **May 18, 2023**—outside the window |
| Patient/type-scoped `DocumentReference` search | No accessible outside-study documentation returned |

**Expected answer:** `{"requested_service": true, "prerequisite": false, "external_study": false}`. Cite the patient, checklist, order and dated procedure; the missing-document claim also requires the scoped search.

**Chart change:** None. Explain the documentation gaps without asserting that the outside study never occurred, or making an approval/denial decision.

<!-- Source: dev task 78dd522360170a1469e78c82; family prior_auth_evidence. -->
