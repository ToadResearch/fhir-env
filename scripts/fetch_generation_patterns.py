#!/usr/bin/env python3
"""Rebuild the pilot catalog from pinned official R4 examples and definitions.

Existing source hashes are checked before replacing the checked-in catalog.
Generation itself uses that catalog offline and needs no downloads.
"""

import argparse
import json
import hashlib
import urllib.request
from pathlib import Path

from fhir_query_rl.generation_pilot import CATALOG_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=Path("artifacts/generation-pilot/standards")
    )
    args = parser.parse_args()
    old = json.loads(CATALOG_PATH.read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    data = {}
    for source in old["sources"]:
        path = args.cache / source["file"]
        raw = (
            path.read_bytes()
            if path.exists()
            else urllib.request.urlopen(source["url"], timeout=30).read()
        )
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("Pinned official source has changed: " + source["file"])
        path.write_bytes(raw)
        data[source["file"]] = json.loads(raw)
    bp = data["observation-example-bloodpressure.json"]
    lipid_bundle = data["diagnosticreport-example-lipids.json"]
    patterns = {
        "fhir_version": "4.0.1",
        "sources": old["sources"],
        "blood_pressure": {
            "code": bp["code"],
            "component_codes": [c["code"] for c in bp["component"]],
        },
        "lipid_tests": [],
        "contracts": {},
    }
    for entry in lipid_bundle["entry"]:
        r = entry["resource"]
        if r["resourceType"] == "Observation" and r["id"] in {
            "cholesterol",
            "triglyceride",
        }:
            patterns["lipid_tests"].append(
                {
                    "code": r["code"],
                    "unit": r["valueQuantity"]["unit"],
                    "unit_code": r["valueQuantity"]["code"],
                }
            )
    for source in old["sources"]:
        if source["resourceType"] != "StructureDefinition":
            continue
        sd = data[source["file"]]
        elements = [e for e in sd["snapshot"]["element"] if e["path"].count(".") == 1]
        patterns["contracts"][sd["type"]] = {
            "required": [e["path"].split(".")[1] for e in elements if e["min"] > 0],
            "choices": {
                e["path"].split(".")[1]: [t["code"] for t in e["type"]]
                for e in elements
                if "[x]" in e["path"]
            },
            "references": {
                e["path"].split(".")[1]: [
                    p.rsplit("/", 1)[-1]
                    for t in e["type"]
                    for p in t.get("targetProfile", [])
                ]
                for e in elements
                if any(t.get("code") == "Reference" for t in e.get("type", []))
            },
        }
    if patterns != old:
        raise ValueError("Rebuilt catalog differs from the pinned catalog")
    CATALOG_PATH.write_text(json.dumps(patterns, indent=2) + "\n")
    print("Verified and reproduced the pinned FHIR R4 generation catalog.")


if __name__ == "__main__":
    main()
