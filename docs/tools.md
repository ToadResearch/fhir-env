# Agent tools: a worked example

[← Benchmark overview](../README.md) · [Tool comparisons and accounting](tools-and-ablations.md)

Follow one fictional patient, **Alex Morgan**, through patient lookup, laboratory
search and a requested duplicate-order cancellation. These are illustrative
examples, not captured benchmark rollouts. Responses are shortened excerpts;
omitted fields and metadata still exist in the actual responses.

Calls below use readable function notation. The agent actually sends structured
MCP/function calls, not Python code. The current harness provides no Python REPL,
shell, filesystem access or web search. Tool responses are JSON-encoded strings.

## Which tools does the agent receive?

| Setting | Available tools |
|---|---|
| `tool_profile="raw"` | `fhir_request` |
| `tool_profile="assisted"` | `fhir_request` plus the five helpers below |
| `query_mode="hybrid"` | Adds `sql_query` to raw or assisted |
| `tool_profile="none"` | No tools; eligible read-only tasks receive chart context directly |

The helpers use visible chart data. They cannot see gold answers or hidden task
contracts. A timeline reference alone does not establish a lab value, and update
preparation does not authorize or commit a change.

## 1. `find_patient`: resolve the patient

**Request:** “Look up Alex Morgan, MRN-1042.”

The user knows an MRN; the agent needs the chart's patient ID.

**Call**

```text
find_patient(identifier="MRN-1042")
```

**Agent sees**

```json
{
  "status": 200,
  "identity_resolution": "unique",
  "body": {
    "total": 1,
    "entry": [{
      "resource": {
        "resourceType": "Patient",
        "id": "p1042",
        "identifier": [{"value": "MRN-1042"}],
        "name": [{"given": ["Alex"], "family": "Morgan"}],
        "birthDate": "1978-04-12"
      }
    }]
  }
}
```

**Next step:** Search using `Patient/p1042`. The helper also accepts `name` and
`birthdate`. Multiple matches return `ambiguous`; no matches return `not_found`.
It does not choose an ambiguous patient.

## 2. `fhir_schema`: inspect supported fields and filters

**Agent needs to know:** “How can I query an Observation?”

**Call**

```text
fhir_schema(resource_type="Observation")
```

**Agent sees** — selected fields from the larger schema response:

```json
{
  "resource_type": "Observation",
  "fields": {
    "status": {
      "enum": [
        "registered", "preliminary", "final", "amended", "corrected",
        "cancelled", "entered-in-error", "unknown"
      ]
    },
    "effectiveDateTime": {"type": "string"}
  },
  "search_parameters": ["code", "date", "encounter", "patient", "status"]
}
```

**Next step:** Construct a supported query. This is structure documentation, not
patient data. The full response also lists required fields and the server's scope.

## 3. `fhir_request`: search, read and change resources

**Request:** “Find this patient's latest final HbA1c result.”

**Call**

```text
fhir_request(
  method="GET",
  path="Observation?patient=p1042&code=4548-4&status=final&_sort=-date&_count=1"
)
```

**Agent sees**

```json
{
  "status": 200,
  "body": {
    "resourceType": "Bundle",
    "type": "searchset",
    "entry": [{
      "resource": {
        "resourceType": "Observation",
        "id": "a1c-17",
        "meta": {"versionId": "1"},
        "status": "final",
        "subject": {"reference": "Patient/p1042"},
        "code": {
          "coding": [{"system": "http://loinc.org", "code": "4548-4"}]
        },
        "effectiveDateTime": "2026-09-15",
        "valueQuantity": {"value": 7.8, "unit": "%"}
      }
    }]
  }
}
```

**Supported answer:** “The latest final HbA1c was 7.8% on September 15, 2026,”
with `Observation/a1c-17` as retrieved evidence. This is a retrieval example;
the agent does not need to interpret the result clinically.

The same tool covers all CRUD operations:

```text
GET    Observation/a1c-17     -> read a known result
POST   Communication         -> create a requested telephone-call record
PUT    ServiceRequest/sr-9    -> replace an existing order with updated fields
DELETE DocumentReference/d-8 -> delete an explicitly authorized duplicate draft
POST   ""                    -> submit an atomic transaction Bundle
```

`body_json` carries a JSON resource or transaction Bundle; `headers_json` carries
headers such as `If-Match`. Updates and deletes require the current version's
ETag. Cancelling a clinical order is a status update, not a DELETE. These are
examples of supported operations, not authorization to perform all of them in
one task.

## 4. `chart_timeline`: find where to look next

**Request:** “Review this patient's recent laboratory results and telephone notes.”

**Call**

```text
chart_timeline(
  patient_id="p1042",
  resource_types="Observation,Communication,DocumentReference",
  start_date="2026-09-01"
)
```

**Agent sees**

```json
{
  "entries": [
    {
      "reference": "Observation/a1c-17",
      "date": "2026-09-15",
      "status": "final",
      "label": {"text": "Hemoglobin A1c"}
    },
    {
      "reference": "DocumentReference/note-22",
      "date": "2026-09-16",
      "status": "current",
      "label": {"text": "Telephone encounter"}
    }
  ],
  "continuation": {},
  "complete": true,
  "errors": [],
  "note": "Index only; read resources/documents for content. Undated entries are retained."
}
```

**Next step:** Read the relevant result or note. The index does not expose the
lab value or note contents. The helper accepts an optional `end_date`, searches
up to six resource types, and retrieves one page per type. Nonempty
`continuation` contains next-page URLs; `complete: false` signals more pages or
errors. Undated entries remain explicitly undated.

## 5. `read_document`: read the actual note

**Agent needs to know:** “What does the telephone note say?”

**Call**

```text
read_document(reference="DocumentReference/note-22")
```

**Agent sees**

```json
{
  "reference": "DocumentReference/note-22",
  "text": "Patient called to request cancellation of the duplicate laboratory order SR-009. Keep the original order active.",
  "total_characters": 112,
  "next_start": null,
  "origin": []
}
```

**Next step:** Find and verify order `SR-009`, including its patient and current
status. The helper decodes supported inline text attachments; it does not fetch
external URLs or perform OCR. Optional `start` and `max_characters` select a
character window. For a longer document, continue at `next_start`.

## 6. `prepare_update`: propose a change before committing

**Request:** “Cancel the duplicate order identified in the telephone note.”

After identifying and verifying `ServiceRequest/sr-9` as order `SR-009`:

**Call**

```text
prepare_update(
  reference="ServiceRequest/sr-9",
  changes_json="{\"status\":\"revoked\"}"
)
```

**Agent sees**

```json
{
  "method": "PUT",
  "path": "ServiceRequest/sr-9",
  "body": {
    "resourceType": "ServiceRequest",
    "id": "sr-9",
    "meta": {"versionId": "3"},
    "identifier": [{"value": "SR-009"}],
    "status": "revoked",
    "intent": "order",
    "subject": {"reference": "Patient/p1042"},
    "code": {"text": "Hemoglobin A1c"}
  },
  "headers": {"If-Match": "W/\"3\""},
  "diff": {
    "status": {"before": "active", "after": "revoked"}
  },
  "committed": false
}
```

**Next step:** Submit the full returned body and headers through `fhir_request`
using PUT. The helper reads the current resource, replaces the supplied
top-level fields, and validates the proposed shape. It preserves other fields
and supplies the current ETag. It does not commit, check hidden task answers,
or decide whether the change is authorized. Use the full tool response, not a
shortened documentation excerpt, when submitting a replacement.

## 7. `sql_query`: retrieve selected fields as a table

**Request:** “Find this patient's latest final HbA1c result.”

This is an alternative retrieval route when hybrid mode is enabled.

**Call** — pass this statement as the tool's `sql` argument:

```sql
SELECT resource_ref, date, value, unit
FROM Observation
WHERE patient_ref = 'Patient/p1042'
  AND code = '4548-4'
  AND status = 'final'
ORDER BY date DESC
LIMIT 1
```

**Agent sees**

```json
{
  "status": 200,
  "columns": ["resource_ref", "date", "value", "unit"],
  "rows": [{
    "resource_ref": "Observation/a1c-17",
    "date": "2026-09-15",
    "value": 7.8,
    "unit": "%"
  }],
  "truncated": false
}
```

**Supported answer:** The same result and evidence reference as the REST query.
Returning only a resource ID would not establish its value. This is the
experimental SQLite projection, not a conformant SQL-on-FHIR ViewDefinition
runner. SELECT queries use explicit columns and return at most 50 rows; SQL
cannot write to the chart.

## Putting the tools together

For an assisted task asking the agent to review the note and cancel its named
duplicate order:

```text
Resolve patient                      find_patient
      |
      v
Find recent events                   chart_timeline
      |
      v
Read telephone note                  read_document
      |
      v
Find + verify duplicate order        fhir_request (GET)
      |
      v
Prepare status update                prepare_update
      |
      v
Commit FHIR update                   fhir_request (PUT + If-Match)
      |
      v
Answer with supporting evidence      final answer; no finish tool
```

This is one possible retrieval path, not a required sequence or minimum call
count. Raw mode can complete the same task through FHIR requests. Schema lookup
is optional when the agent needs help; SQL is an optional retrieval alternative.

## Keeping this guide current

When adding, removing or changing a tool, update its call, response example,
boundaries and profile membership here in the same change. Also update the
[environment tool contract](../environments/fhir_query_rl/README.md#tool-contract)
and [comparison/accounting guide](tools-and-ablations.md) if affected. Keep all
examples fictional, clearly label excerpts, and preserve the distinction between
finding a reference, reading evidence, preparing a change and committing it.

Implementation: [tool registration and FHIR/SQL entry points](../environments/fhir_query_rl/fhir_query_rl/fhir_query_rl.py),
[five optional helpers](../environments/fhir_query_rl/fhir_query_rl/helpers.py),
and [SQLite projections](../environments/fhir_query_rl/fhir_query_rl/sql.py).
