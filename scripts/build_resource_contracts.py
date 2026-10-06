#!/usr/bin/env python3
"""Extract portable structural contracts from the pinned official R4 core package."""

import argparse
import hashlib
import json
from pathlib import Path

from fhir_query_rl.validation import schema


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "package", type=Path, help="Extracted hl7.fhir.r4.core#4.0.1/package directory"
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(
            "environments/fhir_query_rl/fhir_query_rl/resource_contracts.json"
        ),
    )
    args = p.parse_args()
    package = json.loads((args.package / "package.json").read_text())
    if package["name"] != "hl7.fhir.r4.core" or package["version"] != "4.0.1":
        raise ValueError("Expected official R4 4.0.1 core package")
    contracts, hashes = {}, {}
    for kind in sorted(schema()["definitions"]):
        path = args.package / f"StructureDefinition-{kind}.json"
        if not path.exists():
            continue
        sd = json.loads(path.read_text())
        if sd.get("kind") != "resource" or sd.get("abstract"):
            continue
        hashes[kind] = hashlib.sha256(path.read_bytes()).hexdigest()
        elements = []
        for e in sd["snapshot"]["element"][1:]:
            targets = [
                v.rsplit("/", 1)[-1]
                for t in e.get("type", [])
                for v in t.get("targetProfile", [])
            ]
            if e["min"] or e["max"] != "*" or "[x]" in e["path"] or targets:
                elements.append(
                    {
                        "path": e["path"].split(".")[1:],
                        "min": e["min"],
                        "max": e["max"],
                        **(
                            {"types": [t["code"] for t in e["type"]]}
                            if "[x]" in e["path"]
                            else {}
                        ),
                        **({"targets": sorted(set(targets))} if targets else {}),
                    }
                )
        contracts[kind] = elements
    data = {
        "package": package["name"],
        "version": package["version"],
        "source": "https://packages.fhir.org/hl7.fhir.r4.core/4.0.1",
        "structure_definition_sha256": hashes,
        "contracts": contracts,
        "scope": "Nested min/max cardinalities, choice exclusivity, local reference target types; not FHIRPath, terminology or profile validation",
    }
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"resource_types": len(contracts), "output": str(args.output)}))


if __name__ == "__main__":
    main()
