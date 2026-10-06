"""Offline contracts extracted from official R4 StructureDefinition snapshots."""

import json
from functools import lru_cache
from pathlib import Path

from .validation import ref, references


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_suffix(".json").read_text())["contracts"]


def parents(resource, path):
    values = [resource]
    for part in path:
        next_values = []
        for value in values:
            if not isinstance(value, dict):
                continue
            children = (
                [
                    v
                    for k, v in value.items()
                    if k.startswith(part[:-3]) and not k.startswith("_")
                ]
                if part.endswith("[x]")
                else [value.get(part)]
            )
            for child in children:
                next_values.extend(
                    child
                    if isinstance(child, list)
                    else [child]
                    if child is not None
                    else []
                )
        values = next_values
    return [v for v in values if isinstance(v, dict)]


def validate_contracts(resources):
    for resource in resources:
        for c in catalog()[resource["resourceType"]]:
            field = c["path"][-1]
            for parent in parents(resource, c["path"][:-1]):
                names = [field]
                if field.endswith("[x]"):
                    names = [field[:-3] + t[0].upper() + t[1:] for t in c["types"]]
                present = [k for k in names if k in parent or "_" + k in parent]
                if c["max"] == "1" and any(
                    isinstance(parent.get(k), list) for k in present
                ):
                    raise ValueError(
                        f"{ref(resource)}: cardinality {'.'.join(c['path'])} requires a scalar, not an array"
                    )
                if len(present) > 1:
                    raise ValueError(
                        f"{ref(resource)}: multiple choices for {'.'.join(c['path'])}"
                    )
                values = [
                    v
                    for k in present
                    for v in (
                        parent[k]
                        if isinstance(parent.get(k), list)
                        else [parent.get(k)]
                    )
                ]
                if not values and present:
                    values = [None]  # primitive extension-only value
                count = len(values)
                if count < c["min"] or (c["max"] != "*" and count > int(c["max"])):
                    raise ValueError(
                        f"{ref(resource)}: cardinality {'.'.join(c['path'])}={count}, expected {c['min']}..{c['max']}"
                    )
                if c.get("targets") and "Resource" not in c["targets"]:
                    for target in references(values):
                        if target.startswith(("http:", "https:", "urn:", "#")):
                            continue
                        if target.split("/")[0] not in c["targets"]:
                            raise ValueError(
                                f"{ref(resource)}: invalid reference target {field} -> {target}"
                            )
