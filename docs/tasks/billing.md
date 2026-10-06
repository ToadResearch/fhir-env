# Billing

[← Benchmark overview](../../README.md)

Shortened synthetic dev examples. Resolve the supplied MRN and cite supporting records; resource labels are abbreviated.

## Correct a duplicate charge · RU

**Request:** “Apply the billing audit's instruction to correct the duplicate charge. Retain the account and clinical records.”

| Resource | Relevant retrieved information |
|---|---|
| `DocumentReference/audit` | Identifies the duplicate and explicitly instructs marking it entered in error |
| `ChargeItem/duplicate` | `status: billable`; correct patient and charge identifier |
| `Account/account` | `status: active`; patient and insurance links |

**Chart change:** Set only the named ChargeItem status to `entered-in-error`, using its current ETag. Do not delete it or close the account.

**Expected answer:** `{"charge_status": "entered-in-error", "account_status": "active"}`.

<!-- Source: dev task f04b7f4805cf72c271e7b6fe; family duplicate_charge_correction. -->

## Remove an authorized unsubmitted duplicate draft · RD

**Request:** “Billing explicitly authorizes deletion of the named duplicate draft claim. Inspect its linked coverage and matching active account first.”

**Retrieve:** Resolve patient → named draft Claim → its insurance Coverage → matching Account. Check draft status, patient ownership, coverage/account status and current Claim version.

**Chart change:** DELETE only that authorized, unreferenced draft using its ETag. Preserve coverage, account and all clinical records.

**Expected answer:** `{"deleted": true, "clinical_records_changed": false, "coverage_status": "active", "account_status": "active"}`.

This discovery example uses `discovery_variants=true`; submitted claims are outside its deletion authorization.

<!-- Source: dev task e0a5a0c2ecd6d8760414202a; family claim_cleanup_discovery. -->
