# Records

[← Benchmark overview](../../README.md)

A shortened synthetic dev example under an explicitly fictional records-release policy. Resolve the supplied MRN and cite supporting records.

## Check release scope and route expired authorization · CR

**Request:** “The receiving practice requests a transfer summary. Check the named recipient and whether authorization covers today, June 14, 2023. Create the appropriate staff-review task; do not send records.”

| Resource | Relevant retrieved information |
|---|---|
| `CommunicationRequest/transfer` | Request to prepare the transfer summary after checking authorization |
| `Consent/authorization` | `status: active`, but `provision.period.end: 2023-06-12T10:00:00Z`; recipient reference |
| `Organization/recipient` | Receiving practice named in the authorization |
| `DocumentReference/summary` | Prepared coordination summary; does not itself authorize release |

**Chart change:** Create a requested Task linked to the transfer request: “Obtain updated records-release authorization before preparation.” Preserve the Consent and documents; make no external disclosure.

**Expected answer:** `{"authorization_current": false, "release_sent": false}`. An active Consent status does not override its expired scope.

<!-- Source: dev task f554e7452c250604ad7a3f0b; family records_release_scope_review. -->
