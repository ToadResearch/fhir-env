# Registration

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resource labels are abbreviated; the final submission cites the retrieved Patient and Communication.

## Update a verified home phone number · RU

**Request:** “The verified caller reports a new home phone number. Update it without changing other contacts.”

| Resource | Relevant retrieved information |
|---|---|
| `Patient/patient` | Matching MRN, existing `telecom` entries and current version |
| `Communication/verified-call` | Identity verified with MRN/DOB; replace home phone with `202-555-0104`; retain other contacts; address unchanged |

**Chart change:** Replace only the home-phone value in Patient.telecom using the current ETag. Preserve the existing email and any other contacts; do not replace the entire contact list with one phone entry.

**Expected answer:** `{"home_phone": "202-555-0104", "other_contacts_preserved": true}`.

<!-- Source: dev task 5ce11c32fee430703f6cb850; family verified_contact_change. -->
