"""Atomically bundle verified train/dev snapshots; keep evaluation charts private."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from fhir_query_rl.generation import digest


def main():
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/synthetic-hospital-v0.4.0")
    parser.add_argument(
        "--validation-report",
        default="artifacts/generation-v0.4.0/validation-summary.json",
    )
    args = parser.parse_args()
    source = repo / args.source
    target = repo / "environments/fhir_query_rl/fhir_query_rl/training_data"
    manifest = json.loads((source / "manifest.json").read_text())
    for path, sha in manifest["files"].items():
        if digest(source / path) != sha:
            raise ValueError("Source checksum mismatch: " + path)
    if manifest["version"] == "0.4.0":
        if manifest["generation"]["reference_tasks_replayed"] != sum(
            manifest["tasks"].values()
        ):
            raise ValueError("Source is missing reference replay verification")
        report = json.loads((repo / args.validation_report).read_text())
        counts = report["scopes"]["new"]["severity_counts"]
        if counts.get("error", 0) or counts.get("fatal", 0):
            raise ValueError("Independent dev validation failed")
        validation_root = repo / args.validation_report
        for name, sha in report["input_sha256"].items():
            path = validation_root.parent / "validator-inputs" / name
            if digest(path) != sha:
                raise ValueError("Validation input changed")
            if name.startswith("new-"):
                bundle = json.loads(path.read_text())
                shard = (
                    "dev/shards/"
                    + name.removeprefix("new-dev-shards-").removesuffix(".json")
                    + ".ndjson"
                )
                records = [
                    json.loads(line)
                    for line in (source / shard).read_text().splitlines()
                    if line
                ]
                if [e["resource"] for e in bundle["entry"]] != records:
                    raise ValueError("Validation report is for a different chart")
    if target.exists():
        previous = json.loads((target / "manifest.json").read_text())
        for path, sha in previous["files"].items():
            if digest(target / path) != sha:
                raise ValueError(
                    "Packaged files have local changes; refusing to replace them"
                )
    manifest["source_corpus_manifest_sha256"] = digest(source / "manifest.json")
    manifest["packaged_splits"] = ["train", "dev"]
    manifest["files"] = {
        k: v for k, v in manifest["files"].items() if k.startswith(("train/", "dev/"))
    }
    manifest["packaged_tasks"] = {
        k: v for k, v in manifest["tasks"].items() if k in manifest["packaged_splits"]
    }
    manifest["packaged_patients"] = {
        k: v
        for k, v in manifest["patients"].items()
        if k in manifest["packaged_splits"]
    }
    with tempfile.TemporaryDirectory(
        prefix=".training-data-", dir=target.parent
    ) as work:
        staging = Path(work) / "new"
        staging.mkdir()
        for split in manifest["packaged_splits"]:
            shutil.copytree(source / split, staging / split)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        backup = Path(work) / "previous"
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise
    print(
        json.dumps(
            {
                "version": manifest["version"],
                "packaged_splits": manifest["packaged_splits"],
                "packaged_tasks": manifest["packaged_tasks"],
                "bytes": sum(
                    p.stat().st_size for p in target.rglob("*") if p.is_file()
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
