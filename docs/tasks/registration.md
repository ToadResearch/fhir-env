# Registration

[← Benchmark overview](../../README.md)

A shortened synthetic dev example. Resource labels are abbreviated; the final submission cites the retrieved Patient and Communication.

Read each example from **request → retrieval path → chart action → expected answer**. Arrows show retrieval dependencies, not a required number of API calls. Expected JSON shows answer fields; full submissions also include retrieved-resource citations.

## Update a verified home phone number · RU

### Request

> The verified caller reports a new home phone number. Update it without changing other contacts.

### Retrieval path

```text
MRN ---> Patient + current ETag
          | existing telecom entries
          v
    Communication (verified caller)
    new home number + explicit change scope
          |
          v
    update ONLY home-phone value
    retain email and other contacts
```

### Retrieved evidence

- `Patient/patient` — Matching MRN, existing `telecom` entries and current version
- `Communication/verified-call` — Identity verified with MRN/DOB; replace home phone with `202-555-0104`; retain other contacts; address unchanged

### Chart action

Replace only the home-phone value in Patient.telecom using the current ETag. Preserve the existing email and any other contacts; do not replace the entire contact list with one phone entry.

<!-- Source: dev task 5ce11c32fee430703f6cb850; family verified_contact_change. -->

### Expected answer

```json
{
  "home_phone": "202-555-0104",
  "other_contacts_preserved": true
}
```
