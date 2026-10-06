"""Optional assistance over visible chart data; no access to task gold or omissions."""

import base64
import copy
import json
from datetime import date
from urllib.parse import urlencode

from .store import PARAMS, resource_date
from .validation import schema, validate_resource


def output(store, name, payload):
    encoded = json.dumps(payload, ensure_ascii=False)
    store.assistance.append(
        {"tool": name, "round": store.round, "bytes": len(encoded.encode())}
    )
    return encoded


async def find_patient(
    identifier: str = "", name: str = "", birthdate: str = "", _store=None
) -> str:
    """Search candidate patients using supplied facts; never select an ambiguous match.

    Args:
        identifier: MRN value or system|value token, when known.
        name: Supplied patient name, when known.
        birthdate: Exact YYYY-MM-DD date of birth, when known.
    """
    filters = {
        k: v
        for k, v in [
            ("identifier", identifier),
            ("name", name),
            ("birthdate", birthdate),
        ]
        if v
    }
    if not filters or any(len(v) > 256 for v in filters.values()):
        raise ValueError("Provide at least one bounded patient identity field")
    if birthdate:
        date.fromisoformat(birthdate)
    result = _store.request("GET", "Patient?" + urlencode(filters | {"_count": "20"}))
    if result["status"] == 200:
        result["identity_resolution"] = (
            "unique"
            if result["body"]["total"] == 1
            else "ambiguous"
            if result["body"]["total"] > 1
            else "not_found"
        )
    return output(_store, "find_patient", result)


async def fhir_schema(resource_type: str, _store=None) -> str:
    """Inspect official R4 top-level field definitions and simulator search support.

    Args:
        resource_type: Supported FHIR resource type, e.g. Task or Observation.
    """
    if resource_type not in PARAMS:
        raise ValueError("Unsupported resource type")
    definition = schema()["definitions"][resource_type]
    fields = {}
    for key, value in definition.get("properties", {}).items():
        if key.startswith("_"):
            continue
        fields[key] = {
            k: value[k] for k in ("type", "$ref", "enum", "const") if k in value
        }
        if "items" in value:
            fields[key]["items"] = value["items"]
    return output(
        _store,
        "fhir_schema",
        {
            "resource_type": resource_type,
            "required": definition.get("required", []),
            "fields": fields,
            "search_parameters": sorted(PARAMS[resource_type]),
            "scope": "JSON shape and documented research-server subset; not a clinical recommendation",
        },
    )


async def read_document(
    reference: str, start: int = 0, max_characters: int = 6000, _store=None
) -> str:
    """Read an inline UTF-8 DocumentReference attachment as text with bounded paging.

    Args:
        reference: DocumentReference/id found in the chart.
        start: Character offset in concatenated text attachments.
        max_characters: Output length from 1 to 12000.
    """
    if not reference.startswith("DocumentReference/") or reference.count("/") != 1:
        raise ValueError("Use DocumentReference/id")
    if start < 0 or not 1 <= max_characters <= 12000:
        raise ValueError("Invalid document window")
    result = _store.request(
        "GET", reference, visible_fields=["resourceType", "id", "content", "meta"]
    )
    if result["status"] != 200:
        return output(_store, "read_document", result)
    text = []
    for entry in result["body"].get("content", []):
        attachment = entry["attachment"]
        if attachment.get("contentType") not in {
            "text/plain",
            "application/json",
            "text/markdown",
        }:
            continue
        if "data" in attachment:
            text.append(
                base64.b64decode(attachment["data"], validate=True).decode("utf-8")
            )
    if not text:
        raise ValueError("No supported inline text; external URLs are not fetched")
    decoded = "\n\n".join(text)
    end = min(len(decoded), start + max_characters)
    return output(
        _store,
        "read_document",
        {
            "reference": reference,
            "text": decoded[start:end],
            "total_characters": len(decoded),
            "next_start": end if end < len(decoded) else None,
            "origin": result["body"].get("meta", {}).get("tag", []),
        },
    )


async def chart_timeline(
    patient_id: str,
    resource_types: str = "Encounter,Observation,Communication,DocumentReference",
    start_date: str = "",
    end_date: str = "",
    _store=None,
) -> str:
    """Build a dated resource index by executing bounded patient-scoped FHIR searches.

    Args:
        patient_id: Patient/id or id obtained by identity resolution.
        resource_types: Comma-separated types, maximum six; one page per type.
        start_date: Inclusive YYYY-MM-DD index filter, optional.
        end_date: Inclusive YYYY-MM-DD index filter, optional.
    """
    pid = patient_id.removeprefix("Patient/")
    if not pid or any(c in pid for c in "/?&#"):
        raise ValueError("Use a patient id")
    kinds = list(
        dict.fromkeys(x.strip() for x in resource_types.split(",") if x.strip())
    )
    if not 1 <= len(kinds) <= 6 or any(
        "patient" not in PARAMS.get(k, set()) for k in kinds
    ):
        raise ValueError("Choose one to six supported patient-compartment types")
    for value in (start_date, end_date):
        if value:
            date.fromisoformat(value)
    if start_date and end_date and start_date > end_date:
        raise ValueError("Start date exceeds end date")
    entries, continuation, errors = [], {}, []
    for kind in kinds:
        index_fields = [
            "resourceType",
            "id",
            "status",
            "code",
            "type",
            "description",
            "meta",
            "effectiveDateTime",
            "performedDateTime",
            "issued",
            "authoredOn",
            "start",
            "period",
            "date",
            "sent",
            "recorded",
            "dateTime",
            "whenHandedOver",
        ]
        previous_seen = _store.seen.copy()
        previous_fields = {key: fields.copy() for key, fields in _store.exposed.items()}
        result = _store.request(
            "GET",
            kind + "?" + urlencode({"patient": pid, "_count": "50"}),
            visible_fields=index_fields,
        )
        if result["status"] != 200:
            errors.append({"resource_type": kind, "response": result})
            continue
        event = _store.events[-1]
        event["backend_refs"] = event["refs"].copy()
        for link in result["body"].get("link", []):
            if link["relation"] == "next":
                continuation[kind] = link["url"]
        for entry in result["body"].get("entry", []):
            r = entry["resource"]
            when = resource_date(r) or str(
                r.get("date", r.get("sent", r.get("recorded", "")))
            )
            # Undated resources remain explicitly undated rather than assigned an invented date.
            if when and (
                (start_date and when[:10] < start_date)
                or (end_date and when[:10] > end_date)
            ):
                key = entry["fullUrl"]
                if key not in previous_seen:
                    _store.seen.discard(key)
                if key in previous_fields:
                    _store.exposed[key] = previous_fields[key]
                else:
                    _store.exposed.pop(key, None)
                event["refs"].remove(key)
                event["exposed_fields"].pop(key, None)
                continue
            entries.append(
                {
                    "reference": entry["fullUrl"],
                    "date": when or None,
                    "status": r.get("status"),
                    "label": r.get("code", r.get("type", r.get("description", {}))),
                    "origin": r.get("meta", {}).get("tag", []),
                }
            )
    entries.sort(key=lambda e: (e["date"] or "", e["reference"]))
    return output(
        _store,
        "chart_timeline",
        {
            "entries": entries,
            "continuation": continuation,
            "complete": not continuation and not errors,
            "errors": errors,
            "note": "Index only; read resources/documents for content. Undated entries are retained.",
        },
    )


async def prepare_update(reference: str, changes_json: str, _store=None) -> str:
    """Read current resource and prepare a shape-validated replacement, without writing.

    Args:
        reference: Type/id to update after identifying the correct resource.
        changes_json: JSON object of top-level fields to replace, e.g. {"status":"cancelled"}.
    """
    changes = json.loads(changes_json)
    if not isinstance(changes, dict) or not changes or len(changes) > 12:
        raise ValueError("Provide one to twelve field replacements")
    if set(changes) & {"resourceType", "id", "meta"}:
        raise ValueError("Identity and version metadata cannot be replaced")
    result = _store.request("GET", reference)
    if result["status"] != 200:
        return output(_store, "prepare_update", result)
    before = result["body"]
    after = copy.deepcopy(before)
    after.update(changes)
    validate_resource(after)
    return output(
        _store,
        "prepare_update",
        {
            "method": "PUT",
            "path": reference,
            "body": after,
            "headers": {"If-Match": result["headers"]["ETag"]},
            "diff": {
                key: {"before": before.get(key), "after": after[key]} for key in changes
            },
            "committed": False,
            "note": "Agent must assess intent and submit the write; no workflow-gold check.",
        },
    )


HELPERS = [find_patient, fhir_schema, read_document, chart_timeline, prepare_update]
