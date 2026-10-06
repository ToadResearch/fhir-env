"""FHIR R4 shape and local graph checks; not full FHIRPath conformance."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft6Validator


@lru_cache(maxsize=1)
def schema():
    return json.loads((Path(__file__).parent / "schemas/fhir.schema.json").read_text())


@lru_cache(maxsize=128)
def validator(resource_type):
    data = schema()
    if resource_type not in data["definitions"]:
        raise ValueError(f"Unknown FHIR R4 resource type: {resource_type}")
    return Draft6Validator(
        {"$ref": f"#/definitions/{resource_type}", "definitions": data["definitions"]}
    )


def validate_resource(resource):
    kind = resource.get("resourceType", "")
    errors = sorted(validator(kind).iter_errors(resource), key=lambda e: str(e.path))
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors[:4]))


def references(value):
    if isinstance(value, dict):
        if isinstance(value.get("reference"), str):
            yield value["reference"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def ref(resource):
    return f"{resource['resourceType']}/{resource['id']}"


def patient_ref(resource):
    if resource["resourceType"] == "Patient":
        return ref(resource)
    candidates = set()
    for key in ("subject", "patient", "beneficiary", "for"):
        candidates.update(
            x for x in references(resource.get(key)) if x.startswith("Patient/")
        )
    if resource["resourceType"] == "Appointment":
        candidates.update(
            x
            for x in references(resource.get("participant"))
            if x.startswith("Patient/")
        )
    if len(candidates) > 1:
        raise ValueError(
            "Multi-patient resources are outside the benchmark write/search subset"
        )
    return next(iter(candidates), None)


def validate_workflow_graph(index):
    """Selected local workflow constraints; not a full FHIRPath validator."""
    for resource in index.values():
        kind = resource["resourceType"]
        if kind in {"Slot", "Appointment"}:
            start, end = resource.get("start"), resource.get("end")
            if bool(start) != bool(end) or (start and start >= end):
                raise ValueError("Invalid appointment/slot interval")
        if kind == "Appointment" and resource.get("status") == "booked":
            for slot_ref in resource.get("slot", []):
                slot = index.get(slot_ref.get("reference"), {})
                if slot.get("status") not in {"busy", "busy-tentative"}:
                    raise ValueError("Booked appointment requires reserved slot")
                if resource.get("start") < slot.get("start", "") or resource.get(
                    "end"
                ) > slot.get("end", ""):
                    raise ValueError("Appointment falls outside reserved slot")
        if kind == "Observation":
            if resource.get("dataAbsentReason") and any(
                k.startswith("value") for k in resource
            ):
                raise ValueError("Observation cannot have a value and dataAbsentReason")
        if kind == "Immunization":
            if bool(resource.get("occurrenceDateTime")) == bool(
                resource.get("occurrenceString")
            ):
                raise ValueError("Immunization requires exactly one occurrence choice")
        if kind == "MedicationDispense":
            if (
                resource.get("whenPrepared")
                and resource.get("whenHandedOver")
                and resource["whenPrepared"] > resource["whenHandedOver"]
            ):
                raise ValueError("Medication handover precedes preparation")
    occupied = {}
    for resource in index.values():
        if (
            resource["resourceType"] == "Appointment"
            and resource.get("status") == "booked"
        ):
            for slot in references(resource.get("slot")):
                if slot in occupied and not index[slot].get("overbooked"):
                    raise ValueError("Double-booked slot without explicit overbooking")
                occupied[slot] = ref(resource)


def validate_graph(resources, validate_shapes=True):
    index = {ref(r): r for r in resources}
    if len(index) != len(resources):
        raise ValueError("Duplicate resource identity")
    owners = {key: patient_ref(resource) for key, resource in index.items()}
    for key, resource in index.items():
        if validate_shapes:
            validate_resource(resource)
        for target in references(resource):
            if target.startswith(("http:", "https:", "urn:", "#")):
                continue
            if target not in index:
                raise ValueError(f"Broken reference: {key} -> {target}")
            owner, other = owners[key], owners[target]
            if owner and other and owner != other:
                raise ValueError(f"Cross-patient reference: {key} -> {target}")
    validate_workflow_graph(index)
    return index
