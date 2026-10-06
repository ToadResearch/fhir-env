#!/usr/bin/env python3
"""Summarize official-validator output without treating offline checks as complete."""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from fhir_query_rl.validation import ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, nargs="?", default=Path("artifacts/generation-pilot")
    )
    args = parser.parse_args()
    root = args.directory
    result = json.loads((root / "official-validation.json").read_text())
    counts, messages, invalid, resource_counts = (
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(set),
        Counter(),
    )
    details = []
    for entry in result["entry"]:
        outcome = entry["resource"]
        filename = Path(
            next(
                e["valueString"]
                for e in outcome["extension"]
                if e["url"].endswith("operationoutcome-file")
            )
        ).name
        side = filename.split("-", 1)[0]
        bundle = json.loads((root / "validator-inputs" / filename).read_text())
        index = {ref(e["resource"]): e["resource"] for e in bundle["entry"]}
        resource_counts[side] += len(index)
        for issue in outcome.get("issue", []):
            counts[side][issue["severity"]] += 1
            if issue["severity"] not in {"fatal", "error"}:
                continue
            message = issue.get("details", {}).get("text", issue.get("diagnostics", ""))
            messages[side][message] += 1
            expression = " ".join(issue.get("expression", []))
            match = re.search(r"/\*([A-Za-z]+/[a-f0-9]+)\*/", expression)
            key = match.group(1) if match else expression
            invalid[side].add(filename + ":" + key)
            details.append(
                {
                    "side": side,
                    "file": filename,
                    "resource": key,
                    "expression": expression,
                    "message": message,
                    "origin": index.get(key, {}).get("meta", {}).get("tag", []),
                }
            )
    jar = root / "standards/validator_cli-6.10.4.jar"
    summary = {
        "validator": "HL7 FHIR validator 6.10.4",
        "fhir_version": "4.0.1",
        "terminology": "disabled (-tx n/a)",
        "validator_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
        "scopes": {
            side: {
                "resources_validated": resource_counts[side],
                "severity_counts": dict(counts[side]),
                "resources_with_errors": len(invalid[side]),
                "error_messages": dict(messages[side]),
            }
            for side in ("old", "pilot")
        },
        "errors": details,
        "limits": [
            "Old inputs are source-derived plus generated chart resources; candidate inputs are additions plus the same Patient",
            "Candidate merged charts still inherit old errors; scopes have different types and sizes",
            "Offline base R4 validation; not terminology or US Core conformance; warnings remain",
            "No independent-server replay",
        ],
    }
    (root / "validation-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps({"scopes": summary["scopes"], "limits": summary["limits"]}, indent=2)
    )


if __name__ == "__main__":
    main()
