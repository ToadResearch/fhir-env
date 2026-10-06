#!/usr/bin/env python3
"""Freeze additive discovery tasks and verify their train/dev reference plans."""

import argparse
from collections import Counter
import hashlib
import json
import inspect
from pathlib import Path

from fhir_workflows.cli import replay_task
from fhir_workflows.dataset import load_jsonl
from fhir_workflows.discovery import FAMILIES, discovery_variant
from fhir_workflows.fhir_workflows import read_shard
from fhir_workflows.metrics import task_dimensions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "base_manifest_sha256": hashlib.sha256(
            (args.data_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "variant_version": "discovery-v1",
        "variant_compiler_sha256": hashlib.sha256(
            Path(inspect.getfile(discovery_variant)).read_bytes()
        ).hexdigest(),
        "added_resources": 0,
        "model_rollouts": 0,
        "reference_verified": args.verify,
        "splits": {},
    }
    for split in ("train", "dev"):
        variants = []
        for task in load_jsonl(args.data_dir / split / "tasks.jsonl"):
            if task["family"] not in FAMILIES:
                continue
            variant = discovery_variant(
                task, read_shard(str(args.data_dir / task["shard"]))
            )
            if args.verify:
                replay_task(args.data_dir, variant)
            variants.append(variant)
        path = args.output / f"{split}-discovery-tasks.jsonl"
        path.write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in variants))
        report["splits"][split] = {
            "tasks": len(variants),
            "families": dict(Counter(t["family"] for t in variants)),
            "crud": dict(Counter(task_dimensions(t)["crud"] for t in variants)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if split == "dev":
            selected = {t["family"]: t for t in variants}
            examples = [
                {
                    "task_id": t["id"],
                    "prompt": t["prompt"],
                    "dimensions": task_dimensions(t),
                    "expected_answer": t["gold"]["answer"],
                    "reference_steps": t["reference_steps"],
                }
                for t in selected.values()
            ]
            (args.output / "dev-discovery-examples.json").write_text(
                json.dumps(examples, indent=2) + "\n"
            )
    (args.output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
