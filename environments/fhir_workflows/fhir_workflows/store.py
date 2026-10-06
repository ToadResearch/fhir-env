"""Deterministic, bounded REST subset with isolated state, ETags and audits."""

import copy
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from .validation import patient_ref, ref, references, validate_graph, validate_resource


PARAMS = {
    "Patient": {"identifier", "name", "birthdate", "gender"},
    "Encounter": {"patient", "date", "status"},
    "Condition": {"patient", "code", "clinical-status"},
    "AllergyIntolerance": {"patient", "code", "clinical-status"},
    "MedicationStatement": {"patient", "status", "code"},
    "MedicationRequest": {"patient", "status", "code"},
    "Observation": {"patient", "code", "date", "status", "encounter"},
    "DiagnosticReport": {"patient", "code", "date", "status", "based-on"},
    "ServiceRequest": {"patient", "code", "status", "identifier"},
    "Specimen": {"patient", "identifier", "status"},
    "Procedure": {"patient", "code", "status", "date"},
    "Coverage": {"patient", "status", "identifier"},
    "Communication": {"patient", "status", "based-on", "identifier"},
    "Task": {"patient", "status", "focus", "identifier"},
    "Appointment": {"patient", "status", "date"},
    "CarePlan": {"patient", "status"},
    "Goal": {"patient", "lifecycle-status"},
    "Questionnaire": {"identifier", "status"},
    "QuestionnaireResponse": {"patient", "status"},
    "DocumentReference": {"patient", "type", "status"},
    "RelatedPerson": {"patient", "identifier"},
    "Organization": {"identifier", "name"},
    "Practitioner": {"identifier", "name"},
    "PractitionerRole": {"practitioner", "organization"},
    "Location": {"identifier", "name"},
    "Provenance": {"target"},
    "EpisodeOfCare": {"patient", "status", "date"},
    "CareTeam": {"patient", "status"},
    "List": {"patient", "status", "code"},
    "Device": {"patient", "status"},
    "Consent": {"patient", "status"},
    "Composition": {"patient", "status", "type", "date"},
    "MedicationDispense": {"patient", "status", "code"},
}
for _kind in PARAMS:
    if _kind != "Provenance":
        PARAMS[_kind].add("identifier")
PARAMS.update(
    {
        "Schedule": {"identifier", "actor", "active"},
        "Slot": {"identifier", "schedule", "status", "start"},
        "ImagingStudy": {"identifier", "patient", "status", "basedon", "started"},
        "Immunization": {"identifier", "patient", "status", "vaccine-code", "date"},
        "Medication": {"identifier", "status", "code"},
        "MedicationAdministration": {
            "identifier",
            "patient",
            "status",
            "code",
            "request",
            "effective-time",
        },
        "CoverageEligibilityRequest": {"identifier", "patient", "status"},
        "CoverageEligibilityResponse": {"identifier", "patient", "status", "request"},
        "Account": {"identifier", "patient", "status"},
        "ChargeItem": {"identifier", "patient", "code", "context"},
        "Claim": {"identifier", "patient", "status"},
        "DeviceRequest": {"identifier", "patient", "status", "code", "authored-on"},
        "DeviceUseStatement": {"identifier", "patient", "device", "status"},
        "SupplyDelivery": {"identifier", "patient", "status"},
        "NutritionOrder": {"identifier", "patient", "status", "date"},
        "CommunicationRequest": {"identifier", "patient", "status", "authored"},
        "Flag": {"identifier", "patient", "status"},
    }
)
COMMON = {"_id", "_count", "_sort", "_page", "_include"}
INCLUDES = {
    "DiagnosticReport:result": "result",
    "DiagnosticReport:based-on": "basedOn",
    "ServiceRequest:subject": "subject",
    "Task:focus": "focus",
    "Coverage:payor": "payor",
    "Appointment:slot": "slot",
    "Slot:schedule": "schedule",
    "DiagnosticReport:imaging-study": "imagingStudy",
    "MedicationRequest:medication": "medicationReference",
    "MedicationAdministration:request": "request",
    "CoverageEligibilityResponse:request": "request",
    "DeviceUseStatement:device": "device",
}
CODE_FIELDS = {"clinical-status": "clinicalStatus", "code": "code", "type": "type"}
REF_FIELDS = {
    "based-on": "basedOn",
    "focus": "focus",
    "target": "target",
    "encounter": "encounter",
    "practitioner": "practitioner",
    "organization": "organization",
    "schedule": "schedule",
    "actor": "actor",
    "request": "request",
    "basedon": "basedOn",
    "device": "device",
    "context": "context",
}


def values_at(resource, key):
    value = resource.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def token_match(value, needle):
    if isinstance(value, bool):
        return str(value).lower() == needle
    if isinstance(value, str):
        return value == needle
    if not isinstance(value, dict):
        return False
    if "coding" in value:
        return any(token_match(c, needle) for c in value["coding"])
    code = str(value.get("code", value.get("value", "")))
    if "|" in needle:
        system, expected = needle.split("|", 1)
        return (not system or value.get("system") == system) and code == expected
    return code == needle


def resource_date(resource):
    for field in (
        "effectiveDateTime",
        "performedDateTime",
        "issued",
        "authoredOn",
        "start",
        "date",
        "dateTime",
        "sent",
        "recorded",
        "whenHandedOver",
        "occurrenceDateTime",
        "started",
    ):
        if resource.get(field):
            return str(resource[field])
    return str(resource.get("period", {}).get("start", ""))


def date_match(value, search):
    match = re.fullmatch(r"(eq|ne|gt|ge|lt|le)?(\d{4}-\d{2}-\d{2})", search)
    if not match:
        raise ValueError("Only day-precision date predicates are supported")
    op, target = match.groups()
    op = op or "eq"
    value = value[:10]
    return (
        bool(value)
        and {
            "eq": value == target,
            "ne": value != target,
            "gt": value > target,
            "ge": value >= target,
            "lt": value < target,
            "le": value <= target,
        }[op]
    )


class FhirError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class FhirStore:
    def __init__(
        self,
        resources,
        writable_patient=None,
        writable_types=(),
        delete_refs=(),
        max_calls=40,
        writable_refs=(),
    ):
        # Inputs are immutable source snapshots. Tools always return copies;
        # writes replace objects rather than modifying any source object.
        self.initial = {ref(r): r for r in resources}
        self.resources = {
            key: copy.deepcopy(r) if r["resourceType"] in writable_types else r
            for key, r in self.initial.items()
        }
        self.deleted = set()
        self.audit = []
        self.events = []
        self.observed_terms = set()
        self.assistance = []
        self.agent_tools = []
        self.active_tool = "fhir_request"
        self.seen = set()
        self.exposed = {}  # ref -> fields actually returned; SQL ID-only is not full evidence
        self.writable_patient = writable_patient
        self.writable_types = set(writable_types)
        self.delete_refs = set(delete_refs)
        self.writable_refs = set(writable_refs)
        # Explicit auxiliary scope is only valid for existing scheduling slots.
        if any(
            k not in self.initial or self.initial[k]["resourceType"] != "Slot"
            for k in self.writable_refs
        ):
            raise ValueError("Only existing Slots may receive auxiliary write scope")
        self.max_calls = max_calls
        self.round = 0
        self.next_id = 1
        self.current_cost = 0
        self.unsafe_attempts = 0

    def metadata(self):
        return {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "date": "2026-10-01",
            "kind": "instance",
            "fhirVersion": "4.0.1",
            "format": ["json"],
            "implementation": {"description": "Research in-process FHIR subset"},
            "rest": [
                {
                    "mode": "server",
                    "resource": [
                        {
                            "type": kind,
                            "interaction": [
                                {"code": x}
                                for x in ["read", "search-type"]
                                + (
                                    ["create", "update", "delete"]
                                    if kind in self.writable_types
                                    else []
                                )
                            ],
                            "versioning": "versioned-update",
                            "conditionalCreate": kind in self.writable_types,
                            "searchParam": [
                                {
                                    "name": p,
                                    "type": "reference"
                                    if p == "patient" or p in REF_FIELDS
                                    else "date"
                                    if p
                                    in {
                                        "date",
                                        "birthdate",
                                        "start",
                                        "started",
                                        "authored",
                                        "authored-on",
                                        "effective-time",
                                    }
                                    else "string"
                                    if p == "name"
                                    else "token",
                                }
                                for p in sorted(params)
                            ],
                        }
                        for kind, params in PARAMS.items()
                    ],
                }
            ],
        }

    def _matches(self, resource, key, value):
        base, _, modifier = key.partition(":")
        if modifier == "missing":
            if value not in ("true", "false"):
                raise ValueError("missing modifier requires true or false")
            field = CODE_FIELDS.get(base, REF_FIELDS.get(base, base))
            return (not bool(resource.get(field))) == (value == "true")
        if base == "_id":
            return resource["id"] == value
        if base == "patient":
            target = value if "/" in value else f"Patient/{value}"
            if resource["resourceType"] == "Appointment":
                return any(
                    p.get("actor", {}).get("reference") == target
                    for p in resource.get("participant", [])
                )
            return patient_ref(resource) == target
        if base == "identifier":
            return any(token_match(v, value) for v in resource.get("identifier", []))
        if base == "name":
            return any(
                value.lower()
                in (
                    str(v.get("text", ""))
                    + " "
                    + str(v.get("family", ""))
                    + " "
                    + " ".join(v.get("given", []))
                ).lower()
                if isinstance(v, dict)
                else value.lower() in str(v).lower()
                for v in values_at(resource, "name")
            )
        if base == "birthdate":
            return date_match(resource.get("birthDate", ""), value)
        if base in {
            "date",
            "start",
            "started",
            "effective-time",
            "authored-on",
            "authored",
        }:
            return date_match(resource_date(resource), value)
        if base in REF_FIELDS:
            return any(
                target == value or target.split("/")[-1] == value
                for v in values_at(resource, REF_FIELDS[base])
                for target in references(v)
            )
        field = CODE_FIELDS.get(
            base,
            {"lifecycle-status": "lifecycleStatus", "vaccine-code": "vaccineCode"}.get(
                base, base
            ),
        )
        if base == "code" and resource["resourceType"] in {
            "MedicationStatement",
            "MedicationRequest",
            "MedicationAdministration",
            "MedicationDispense",
            "DeviceRequest",
        }:
            field = (
                "codeCodeableConcept"
                if resource["resourceType"] == "DeviceRequest"
                else "medicationCodeableConcept"
            )
            target = resource.get("medicationReference", {}).get("reference")
            if target in self.resources:
                return token_match(self.resources[target].get("code"), value)
        return any(token_match(v, value) for v in values_at(resource, field))

    def _search(self, kind, pairs):
        if kind not in PARAMS:
            raise FhirError(400, f"Unsupported resource: {kind}")
        include = []
        for key, value in pairs:
            base = key.split(":")[0]
            if key == "patient.identifier":
                if "patient" not in PARAMS[kind]:
                    raise FhirError(
                        400, "Patient chaining unsupported for this resource"
                    )
                continue
            if key == "_include":
                if value not in INCLUDES or not value.startswith(kind + ":"):
                    raise FhirError(400, "Unsupported include")
                include.append(value)
            elif key not in COMMON and (
                base not in PARAMS[kind]
                or (":" in key and not key.endswith(":missing"))
            ):
                raise FhirError(400, f"Unsupported parameter: {key}")
        candidates = [r for r in self.resources.values() if r["resourceType"] == kind]
        for key, value in pairs:
            if key in COMMON - {"_id"}:
                continue
            if key == "patient.identifier":
                ids = {
                    ref(p)
                    for p in self.resources.values()
                    if p["resourceType"] == "Patient"
                    and self._matches(p, "identifier", value)
                }
                candidates = [r for r in candidates if patient_ref(r) in ids]
            else:
                candidates = [
                    r
                    for r in candidates
                    if any(self._matches(r, key, choice) for choice in value.split(","))
                ]
        params = dict(pairs)
        order = params.get("_sort", "_id")
        if order.lstrip("-") not in ("date", "_id"):
            raise FhirError(400, "Only date and _id sort are supported")
        candidates.sort(
            key=(resource_date if order.lstrip("-") == "date" else lambda r: r["id"]),
            reverse=order.startswith("-"),
        )
        count, page = int(params.get("_count", "10")), int(params.get("_page", "0"))
        if not 1 <= count <= 50 or not 0 <= page <= 10000:
            raise FhirError(400, "Invalid page size or offset")
        selected = candidates[page * count : (page + 1) * count]
        entries = [
            {
                "fullUrl": ref(r),
                "resource": copy.deepcopy(r),
                "search": {"mode": "match"},
            }
            for r in selected
        ]
        included = set(ref(r) for r in selected)
        for directive in include:
            field = INCLUDES[directive]
            for resource in selected:
                for target in references(resource.get(field)):
                    if target in self.resources and target not in included:
                        entries.append(
                            {
                                "fullUrl": target,
                                "resource": copy.deepcopy(self.resources[target]),
                                "search": {"mode": "include"},
                            }
                        )
                        included.add(target)
        bundle = {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(candidates),
            "entry": entries,
        }
        if (page + 1) * count < len(candidates):
            rest = [(k, v) for k, v in pairs if k != "_page"] + [
                ("_page", str(page + 1))
            ]
            bundle["link"] = [{"relation": "next", "url": kind + "?" + urlencode(rest)}]
        return bundle

    def _authorize(self, resource, delete=False):
        if resource.get("resourceType") == "Slot" and resource.get("id"):
            original = self.initial.get(ref(resource))
            if original and any(
                resource.get(k) != original.get(k)
                for k in (set(resource) | set(original)) - {"status", "meta"}
            ):
                self.unsafe_attempts += 1
                raise FhirError(
                    403, "Auxiliary Slot permission allows status changes only"
                )
        if (
            resource["resourceType"] not in self.writable_types
            or (
                patient_ref(resource) != self.writable_patient
                and (not resource.get("id") or ref(resource) not in self.writable_refs)
            )
            or (delete and ref(resource) not in self.delete_refs)
        ):
            self.unsafe_attempts += 1
            raise FhirError(
                403, "Write outside this episode's patient/type/delete policy"
            )

    def _execute(self, method, path, body, headers):
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or ".." in path or len(path) > 8192:
            raise FhirError(400, "Use bounded relative FHIR paths")
        clean = parsed.path.strip("/")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if method == "GET" and clean == "metadata":
            return 200, self.metadata(), {}
        parts = clean.split("/") if clean else []
        if method == "GET":
            if len(parts) == 1:
                return 200, self._search(clean, pairs), {}
            if len(parts) == 2 and not pairs:
                if clean in self.deleted:
                    raise FhirError(410, "Resource deleted")
                if clean not in self.resources:
                    raise FhirError(404, "Resource not found")
                r = self.resources[clean]
                return 200, copy.deepcopy(r), {"ETag": f'W/"{r["meta"]["versionId"]}"'}
            raise FhirError(400, "Unsupported GET path")
        if method == "POST" and not clean:
            if (
                not isinstance(body, dict)
                or body.get("resourceType") != "Bundle"
                or body.get("type") != "transaction"
            ):
                raise FhirError(400, "POST root requires a transaction Bundle")
            entries = body.get("entry", [])
            if not entries or len(entries) > 20:
                raise FhirError(400, "Transaction must have 1–20 entries")
            results = []
            snapshot = (
                self.resources.copy(),
                self.deleted.copy(),
                self.audit.copy(),
                self.next_id,
            )
            try:
                for entry in entries:
                    request = entry["request"]
                    verb, url = request["method"], request["url"]
                    if verb not in {"POST", "PUT", "DELETE"} or not url:
                        raise FhirError(
                            400, "Only non-nested write transactions are supported"
                        )
                    self.current_cost += 1
                    status, payload, hdr = self._execute(
                        verb,
                        url,
                        entry.get("resource"),
                        {
                            k: v
                            for k, v in {
                                "If-Match": request.get("ifMatch"),
                                "If-None-Exist": request.get("ifNoneExist"),
                            }.items()
                            if v
                        },
                    )
                    results.append(
                        {
                            "response": {
                                "status": str(status),
                                **(
                                    {"location": hdr["Location"]}
                                    if "Location" in hdr
                                    else {}
                                ),
                            },
                            **({"resource": payload} if payload else {}),
                        }
                    )
                validate_graph(list(self.resources.values()), validate_shapes=False)
            except Exception:
                self.resources, self.deleted, self.audit, self.next_id = snapshot
                raise
            return (
                200,
                {
                    "resourceType": "Bundle",
                    "type": "transaction-response",
                    "entry": results,
                },
                {},
            )
        if method == "POST" and len(parts) == 1 and not pairs:
            if not isinstance(body, dict) or body.get("resourceType") != clean:
                raise FhirError(400, "POST resource type mismatch")
            r = copy.deepcopy(body)
            if r.get("id"):
                raise FhirError(400, "Server assigns IDs on create")
            self._authorize(r)
            conditional = headers.get("If-None-Exist")
            if conditional is not None:
                conditions = parse_qsl(conditional, keep_blank_values=True)
                if not conditions or any(
                    key.startswith("_") and key != "_id" for key, _ in conditions
                ):
                    raise FhirError(
                        400,
                        "Conditional create requires resource filters only; paging, controls and includes are unsupported",
                    )
                matches = self._search(clean, conditions)
                if matches["total"] > 1:
                    raise FhirError(412, "Conditional create is ambiguous")
                if matches["total"] == 1:
                    existing = matches["entry"][0]["resource"]
                    return 200, existing, {"Location": ref(existing)}
            r["id"] = f"created-{self.next_id:05d}"
            while ref(r) in self.resources:
                self.next_id += 1
                r["id"] = f"created-{self.next_id:05d}"
            self.next_id += 1
            r["meta"] = {**r.get("meta", {}), "versionId": "1"}
            validate_resource(r)
            self.resources[ref(r)] = r
            self.audit.append(
                {
                    "method": method,
                    "ref": ref(r),
                    "before": None,
                    "after": copy.deepcopy(r),
                }
            )
            return 201, copy.deepcopy(r), {"Location": ref(r), "ETag": 'W/"1"'}
        if len(parts) != 2 or pairs or method not in {"PUT", "DELETE"}:
            raise FhirError(400, "Supported methods: GET, POST, PUT, DELETE; no upsert")
        if clean not in self.resources:
            raise FhirError(404, "Resource not found")
        old = self.resources[clean]
        self._authorize(old, delete=method == "DELETE")
        if headers.get("If-Match") != f'W/"{old["meta"]["versionId"]}"':
            raise FhirError(412, "A current If-Match ETag is required")
        if method == "DELETE":
            if any(
                clean in references(r)
                for key, r in self.resources.items()
                if key != clean
            ):
                raise FhirError(409, "Resource is referenced")
            self.deleted.add(clean)
            del self.resources[clean]
            self.audit.append(
                {
                    "method": method,
                    "ref": clean,
                    "before": copy.deepcopy(old),
                    "after": None,
                }
            )
            return 204, None, {}
        if not isinstance(body, dict) or ref(body) != clean:
            raise FhirError(400, "PUT identity mismatch")
        self._authorize(body)
        r = copy.deepcopy(body)
        r["meta"] = {
            **r.get("meta", {}),
            "versionId": str(int(old["meta"]["versionId"]) + 1),
        }
        validate_resource(r)
        self.resources[clean] = r
        self.audit.append(
            {
                "method": method,
                "ref": clean,
                "before": copy.deepcopy(old),
                "after": copy.deepcopy(r),
            }
        )
        return 200, copy.deepcopy(r), {"ETag": f'W/"{r["meta"]["versionId"]}"'}

    def observe(self, resource, fields=None):
        key = ref(resource)
        self.seen.add(key)
        self.exposed.setdefault(key, set()).update(
            resource.keys() if fields is None else fields
        )

    def observe_response_terms(self, text):
        from .metrics import lexical_terms

        self.observed_terms.update(lexical_terms(text))

    def request(self, method, path, body=None, headers=None, visible_fields=None):
        method = method.upper()
        self.current_cost = 0 if method == "POST" and not path.strip("/") else 1
        before = (
            (
                self.resources.copy(),
                self.deleted.copy(),
                self.audit.copy(),
                self.next_id,
            )
            if method != "GET"
            else None
        )
        try:
            if len(self.events) >= self.max_calls:
                raise FhirError(429, "Episode tool-call budget exhausted")
            status, payload, response_headers = self._execute(
                method, path, body, headers or {}
            )
            if method != "GET":
                validate_graph(list(self.resources.values()), validate_shapes=False)
            result = {"status": status, "headers": response_headers, "body": payload}
        except (FhirError, ValueError, KeyError, TypeError, IndexError) as error:
            if before is not None:
                self.resources, self.deleted, self.audit, self.next_id = before
            status = error.status if isinstance(error, FhirError) else 400
            result = {
                "status": status,
                "headers": {},
                "body": {
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "processing",
                            "diagnostics": str(error),
                        }
                    ],
                },
            }
        returned = []
        if result["status"] < 300 and result["body"]:
            payload = result["body"]
            if payload.get("resourceType") == "Bundle":
                returned = [
                    e["resource"] for e in payload.get("entry", []) if "resource" in e
                ]
            elif payload.get("id"):
                returned = [payload]
            for resource in returned:
                self.observe(resource, visible_fields)
        self.events.append(
            {
                "route": "rest",
                "via_tool": self.active_tool,
                "method": method,
                "query": path,
                "status": result["status"],
                "round": self.round,
                "refs": [ref(r) for r in returned],
                "exposed_fields": {ref(r): list(r) for r in returned},
                "bytes": len(json.dumps(result).encode()),
                "primitive_operations": self.current_cost,
            }
        )
        if visible_fields is not None:
            self.events[-1]["exposed_fields"] = {
                ref(r): list(visible_fields) for r in returned
            }
        if (
            isinstance(result["body"], dict)
            and result["body"].get("type") == "searchset"
        ):
            self.events[-1]["total"] = result["body"]["total"]
        from .metrics import filter_terms, query_features

        self.events[-1]["query_features"] = query_features(self.events[-1])
        self.events[-1]["filter_terms_not_previously_observed"] = sorted(
            filter_terms(self.events[-1]) - self.observed_terms
        )
        if visible_fields is None and self.active_tool in {None, "fhir_request"}:
            self.observe_response_terms(json.dumps(result))
        return result
