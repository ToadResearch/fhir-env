#!/usr/bin/env python3
"""Package completed evaluations for review. Never delete or modify source runs.

Uses only the standard library, including on HiPerGator's Python 3.9.
This is an analysis bundle, not a backup of models, environments or datasets.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from collect_hipergator_results import DATA_PATTERNS, EXCLUDED

PRESETS = ("gpt-oss-20b", "glimmer", "k2-horizon-7b", "lfm2-5-24b-a2b")


def episode_count(stream):
    count = 0
    for line in stream:
        if not line.strip():
            continue
        item = json.loads(line)
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("task"), dict)
            or not isinstance(item.get("traces"), list)
        ):
            raise TypeError("Expected native Verifiers episodes")
        if any(
            not isinstance(t, dict) or t.get("version") != 1 for t in item["traces"]
        ):
            raise ValueError("Expected native trace version 1")
        count += 1
    return count


def select_runs(base, batch):
    """An explicit batch prevents mixing unrelated runs or cancelled duplicates."""
    chosen = {}
    for preset in PRESETS:
        run = base / (batch + "-" + preset)
        for path in (
            run,
            run / "plan.json",
            run / "receipts",
            run / "receipts/finished.json",
            run / "evals",
        ):
            if path.is_symlink():
                raise ValueError("Refusing symlinked input: " + str(path))
        plan = json.loads((run / "plan.json").read_text())
        receipt = json.loads((run / "receipts/finished.json").read_text())
        if not isinstance(plan, dict) or not isinstance(receipt, dict):
            raise TypeError("Expected plan and finished receipt objects: " + run.name)
        if plan.get("preset") != preset:
            raise ValueError("Model preset mismatch: " + run.name)
        if receipt.get("status") != "completed":
            raise ValueError("Evaluation is not completed: " + run.name)
        traces = list((run / "evals").glob("*/traces.jsonl"))
        if not traces:
            raise ValueError("No traces.jsonl: " + run.name)
        count = 0
        for trace in traces:
            if trace.is_symlink() or trace.parent.is_symlink():
                raise ValueError("Refusing symlinked trace: " + str(trace))
            with trace.open("rb") as stream:
                count += episode_count(stream)
        if (
            count == 0
            or count
            != plan.get("num_examples", 0) * plan.get("rollouts_per_example", 1)
            or count != receipt.get("episodes")
        ):
            raise ValueError("Episode count is empty or inconsistent: " + run.name)
        chosen[preset] = (run, count, traces)
    return chosen


def export_files(run):
    yield run / "plan.json"
    for folder in ("evals", "logs", "receipts"):
        root = run / folder
        if root.is_symlink():
            raise ValueError("Refusing symlinked output: " + str(root))
        for directory, dirs, files in os.walk(root, followlinks=False):
            directory = Path(directory)
            dirs[:] = [
                name
                for name in dirs
                if not (directory / name).is_symlink()
                and not any(fnmatch.fnmatch(name, p) for p in EXCLUDED)
            ]
            for name in files:
                path = directory / name
                if path.is_symlink() or any(fnmatch.fnmatch(name, p) for p in EXCLUDED):
                    continue
                if any(fnmatch.fnmatch(name, p) for p in DATA_PATTERNS):
                    yield path


def digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def bundle(base, batch, output):
    if not batch or Path(batch).name != batch or batch in (".", ".."):
        raise ValueError("Batch must be a single directory-name prefix")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite an archive: " + str(output))
    selected = select_runs(base, batch)  # Validate all four before creating anything.
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=".fhir-review-", suffix=".zip", dir=output.parent
    )
    os.close(fd)
    temporary = Path(name)
    manifest = {
        "batch": batch,
        "runs": {},
        "files_sha256": {},
        "purpose": "analysis bundle; source runs retained",
    }
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for preset, (run, count, traces) in selected.items():
                manifest["runs"][preset] = {
                    "directory": run.name,
                    "episodes": count,
                    "traces": [str(t.relative_to(base)) for t in traces],
                }
                for path in sorted(export_files(run)):
                    relative = str(path.relative_to(base))
                    with path.open("rb") as stream:
                        manifest["files_sha256"][relative] = digest(stream)
                    archive.write(path, relative)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity check failed")
            for relative, expected in manifest["files_sha256"].items():
                with archive.open(relative) as stream:
                    if digest(stream) != expected:
                        raise ValueError("Source changed during export: " + relative)
            for result in manifest["runs"].values():
                count = 0
                for relative in result["traces"]:
                    with archive.open(relative) as stream:
                        count += episode_count(stream)
                if count != result["episodes"]:
                    raise ValueError("Archived episode count mismatch")
        # Publish without replacing a prior archive, including a concurrent writer.
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs/hipergator",
    )
    parser.add_argument(
        "--batch",
        required=True,
        help="Exact common prefix used in --run-dir PREFIX-PRESET",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = bundle(args.base, args.batch, args.output)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, "No verified archive published: " + str(error) + "\n")
    for preset, result in manifest["runs"].items():
        print(
            f"{preset}: {result['episodes']} verified episodes ({result['directory']})"
        )
    print("Archive: " + str(args.output.resolve()))
    print("All source folders retained. This command has no cleanup mode.")


if __name__ == "__main__":
    main()
