#!/usr/bin/env python3
"""Create an auditable same-cohort comparison, review excerpts and validator inputs."""

import argparse
import base64
import json
from collections import Counter, defaultdict
from pathlib import Path

from fhir_query_rl.dataset import load_jsonl
from fhir_query_rl.generation import digest
from fhir_query_rl.validation import ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("data/synthetic-hospital-v0.3.0")
    )
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/synthetic-hospital-v0.4.0")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/generation-v0.4.0")
    )
    parser.add_argument("--validator-splits", nargs="+", default=["dev"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifests = [
        json.loads((r / "manifest.json").read_text())
        for r in (args.baseline, args.dataset)
    ]
    old, new = manifests
    for root, manifest in zip((args.baseline, args.dataset), manifests):
        for filename, sha in manifest["files"].items():
            if digest(root / filename) != sha:
                raise ValueError("Checksum mismatch: " + str(root / filename))
    snapshot_indexes = {}
    preservation = Counter()
    patient_sets = defaultdict(set)
    for shard in (p for p in old["files"] if p.endswith(".ndjson")):
        baseline = {ref(r): r for r in load_jsonl(args.baseline / shard)}
        expanded = {ref(r): r for r in load_jsonl(args.dataset / shard)}
        if not set(baseline) <= set(expanded):
            raise ValueError("Original resource identity lost")
        for key, r in baseline.items():
            if any(
                t.get("code") == "source-preserved"
                for t in r.get("meta", {}).get("tag", [])
            ):
                if expanded[key] != r:
                    raise ValueError("Source content changed: " + key)
                preservation["source_documents_unchanged"] += 1
            preservation["original_resources_retained"] += 1
        split = shard.split("/")[0]
        patient_sets[split].update(
            key for key in expanded if key.startswith("Patient/")
        )
        if split in args.validator_splits:
            snapshot_indexes[shard] = expanded
            for side, records in (("old", baseline), ("new", expanded)):
                path = (
                    args.output
                    / "validator-inputs"
                    / (side + "-" + shard.replace("/", "-").replace(".ndjson", ".json"))
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                bundle = {
                    "resourceType": "Bundle",
                    "type": "collection",
                    "entry": [
                        {
                            "fullUrl": "https://fhir-query-rl.example/fhir/" + key,
                            "resource": r,
                        }
                        for key, r in records.items()
                    ],
                }
                path.write_text(json.dumps(bundle))
    for split, patients in patient_sets.items():
        if any(
            patients & other for name, other in patient_sets.items() if name != split
        ):
            raise ValueError("Patient crossed a split")
    summary = {
        "baseline_manifest_sha256": digest(args.baseline / "manifest.json"),
        "new_manifest_sha256": digest(args.dataset / "manifest.json"),
        "patients": new["patients"],
        "preservation": dict(preservation),
        "original_input_unchanged": True,
        "base_family_coverage_retained": set(old["families"]) <= set(new["families"]),
        "base_resource_types_retained": set(old["resources"]) <= set(new["resources"]),
        "added_families": sorted(set(new["families"]) - set(old["families"])),
        "added_types": sorted(set(new["resources"]) - set(old["resources"])),
        "baseline": {
            "tasks": sum(old["tasks"].values()),
            "non_provenance_resources": sum(
                v for k, v in old["resources"].items() if k != "Provenance"
            ),
            "types": len(old["resources"]),
            "families": old["distinct_families"],
            **new["structure"]["baseline"],
        },
        "enhanced": {
            "tasks": sum(new["tasks"].values()),
            "non_provenance_resources": sum(
                v for k, v in new["resources"].items() if k != "Provenance"
            ),
            "types": len(new["resources"]),
            "families": new["distinct_families"],
            **new["structure"]["enhanced"],
        },
        "generation": new["generation"],
        "validation_scope": {
            "splits": args.validator_splits,
            "includes_provenance": True,
            "same_patients_and_full_charts": True,
        },
        "limits": [
            "More records can increase diversity; these whole-corpus counts are not a count-controlled causal comparison.",
            "Reference plans are executable correctness oracles, not model rollout results.",
            "Authored structural fixtures; no clinician adjudication, physiology simulation or representative workflow prevalence.",
        ],
    }
    from compare_generation_pilot import (
        choose_cohort,
        closure,
        generated,
        balanced_comparison,
    )

    try:
        cohort, baseline_shards = choose_cohort(
            args.baseline, load_jsonl(args.baseline / "dev/tasks.jsonl")
        )
        expanded_shards = {s: load_jsonl(args.dataset / s) for s in baseline_shards}
        old_records, new_records = [], []
        for patient, task, _, _ in cohort:
            old_records.extend(
                r
                for r in closure(baseline_shards[task["shard"]], patient)
                if generated(r)
            )
            new_records.extend(
                r
                for r in closure(expanded_shards[task["shard"]], patient)
                if generated(r)
            )
        balanced = balanced_comparison(old_records, new_records, 20261006)
        balanced["enhanced"] = balanced.pop("pilot")
        summary["balanced_comparison"] = balanced
        (args.output / "balanced-comparison.json").write_text(
            json.dumps(balanced, indent=2) + "\n"
        )
    except ValueError as error:
        summary["balanced_comparison"] = {"not_computed": str(error)}
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Generation v0.4.0 comparison",
        "",
        "Same source patients and splits. The expanded corpus retains the original resource IDs and task families.",
        "",
        "| Measure | Original | Enhanced |",
        "|---|---:|---:|",
    ]
    for label, key in (
        ("Resources excluding Provenance", "non_provenance_resources"),
        ("FHIR resource types", "types"),
        ("Task instances", "tasks"),
        ("Task families", "families"),
        ("Structural shapes", "distinct_shapes"),
        ("Reference edge types", "reference_edge_types"),
    ):
        lines.append(
            f"| {label} | {summary['baseline'][key]:,} | {summary['enhanced'][key]:,} |"
        )
    lines += [
        "",
        "All task reference plans pass strict answer/evidence/mutation checks. Source documents remain exact. Independent validator results are recorded separately.",
        "",
        "New types: " + ", ".join(summary["added_types"]) + ".",
        "",
        *summary["limits"],
    ]
    (args.output / "comparison.md").write_text("\n".join(lines) + "\n")
    examples = [
        "# Manual review pack",
        "",
        "Actual serialized records, abbreviated for inspection. Check the corresponding complete shard and generation sidecars for all fields.",
    ]
    cases = load_jsonl(args.dataset / "generation/episodes.jsonl")
    selected = []
    for age in ("child", "adult"):
        for branch in ("final", "pending", "rejected", "corrected"):
            case = next(
                (
                    c
                    for c in cases
                    if c["shard"] in snapshot_indexes
                    and (c["plan"]["age"] < 18) == (age == "child")
                    and c["plan"]["lab_branch"] == branch
                ),
                None,
            )
            if case:
                selected.append(case)
    for case in selected:
        p = case["plan"]
        records = snapshot_indexes[case["shard"]]
        examples += [
            "",
            f"## Age {p['age']}; {p['lab_branch']}; {p['panel_encoding']}",
            "",
            f"Patient `{p['patient_id']}`; shard `{case['shard']}`.",
        ]
        for label in (
            "lab-order",
            "lab-specimen",
            "lab-report",
            "dispense",
            "appointment",
            "transfer-list",
            "duplicate-review",
            "intake-detail",
        ):
            r = records[case["labels"][label]]
            reduced = {
                k: v for k, v in r.items() if k not in {"text", "identifier", "meta"}
            }
            examples += ["", "```json", json.dumps(reduced, indent=2), "```"]
        doc = records[case["labels"]["transfer-summary"]]
        text = base64.b64decode(doc["content"][0]["attachment"]["data"]).decode()
        examples += ["", "Narrative: " + text]
    (args.output / "review-pack.md").write_text("\n".join(examples) + "\n")
    print(
        json.dumps(
            {
                "comparison": str(args.output / "comparison.md"),
                "review_pack": str(args.output / "review-pack.md"),
                "validator_splits": args.validator_splits,
            }
        )
    )


if __name__ == "__main__":
    main()
