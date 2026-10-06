# Insurance

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resolve the supplied MRN and cite supporting records; resource labels are abbreviated.

## Check coverage for a specific service date · R

**Request:** “Registration labels the policy active. Check the insurer's response for the planned service date, March 16, 2021.”

| Resource | Relevant retrieved fields |
|---|---|
| `Coverage/policy` | `status: active`; beneficiary and policy identifiers |
| `CoverageEligibilityRequest/check` | `servicedDate: 2021-03-16`; `insurance.coverage` links the policy |
| `CoverageEligibilityResponse/reply` | Matching request/date/policy; `outcome: complete`; `insurance.inforce: false` |

**Expected answer:** `{"service_date": "2021-03-16", "in_force": false, "payment_guaranteed": false}`. No chart changes.

The registration status alone does not answer the dated eligibility question. This task reports documented eligibility; it does not make a payment guarantee.

<!-- Source: dev task 7f8e555d0324298c339aa90c; family date_specific_eligibility. -->
