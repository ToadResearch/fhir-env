#!/usr/bin/env python3
"""Run pinned independent R4 validation and fail the new corpus on any error."""

import argparse
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from fhir_query_rl.generation import digest

JAR_SHA256 = "1106b9d58f9e363e47bea7c4fc065841e5fc91fe9d062775c3bfdd212bd653cc"


def summarize(root, jar):
    result = json.loads((root / "official-validation.json").read_text())
    counts, resources, errors = defaultdict(Counter), Counter(), []
    input_hashes = {}
    for entry in result["entry"]:
        outcome = entry["resource"]
        name = Path(
            next(
                e["valueString"]
                for e in outcome["extension"]
                if e["url"].endswith("operationoutcome-file")
            )
        ).name
        side = name.split("-", 1)[0]
        path = root / "validator-inputs" / name
        bundle = json.loads(path.read_text())
        input_hashes[name] = digest(path)
        resources[side] += len(bundle["entry"])
        for issue in outcome.get("issue", []):
            counts[side][issue["severity"]] += 1
            if issue["severity"] in {"error", "fatal"}:
                errors.append(
                    {
                        "side": side,
                        "file": name,
                        "message": issue.get("details", {}).get(
                            "text", issue.get("diagnostics", "")
                        ),
                        "expression": issue.get("expression", []),
                    }
                )
    expected = {p.name for p in (root / "validator-inputs").glob("*.json")}
    if set(input_hashes) != expected:
        raise ValueError("Validator did not return an outcome for every input")
    summary = {
        "validator": "HL7 FHIR validator 6.10.4",
        "validator_jar_sha256": digest(jar),
        "fhir_version": "4.0.1",
        "terminology": "disabled (-tx n/a)",
        "input_sha256": input_hashes,
        "scopes": {
            s: {"resources": resources[s], "severity_counts": dict(counts[s])}
            for s in sorted(resources)
        },
        "errors": errors,
        "limits": [
            "Full dev chart bundles, including Provenance; same patients in baseline and enhanced inputs.",
            "Base R4 structural and FHIRPath validation. Terminology, US Core, independent-server execution and clinical review are not certified.",
        ],
    }
    (root / "validation-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "scopes": summary["scopes"],
                "new_errors": sum(e["side"] == "new" for e in errors),
            }
        )
    )
    if "new" not in resources or counts["new"]["error"] + counts["new"]["fatal"]:
        raise SystemExit("Enhanced corpus failed independent validation")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "directory", type=Path, nargs="?", default=Path("artifacts/generation-v0.4.0")
    )
    p.add_argument(
        "--jar",
        type=Path,
        default=Path("artifacts/generation-pilot/standards/validator_cli-6.10.4.jar"),
    )
    p.add_argument(
        "--cache-home",
        type=Path,
        default=Path("artifacts/generation-pilot/validator-home"),
    )
    p.add_argument("--summarize-only", action="store_true")
    args = p.parse_args()
    if digest(args.jar) != JAR_SHA256:
        raise ValueError("Validator binary differs from the pinned 6.10.4 release")
    if not args.summarize_only:
        started = time.monotonic()
        command = [
            "java",
            "-Duser.home=" + str(args.cache_home.resolve()),
            "-Xmx3g",
            "-jar",
            str(args.jar),
            str(args.directory / "validator-inputs"),
            "-version",
            "4.0.1",
            "-tx",
            "n/a",
            "-output",
            str(args.directory / "official-validation.json"),
        ]
        with (args.directory / "official-validation.log").open("w") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        (args.directory / "validator-run.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "exit_code": result.returncode,
                    "seconds": time.monotonic() - started,
                },
                indent=2,
            )
            + "\n"
        )
        if (
            result.returncode
            or not (args.directory / "official-validation.json").exists()
        ):
            raise SystemExit("Validator did not complete; inspect its log")
    summarize(args.directory, args.jar)


if __name__ == "__main__":
    main()
