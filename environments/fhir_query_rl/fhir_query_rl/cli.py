import argparse
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .dataset import build_dataset, load_jsonl
from .scoring import evaluate
from .store import FhirStore
from .validation import ref, validate_graph


@lru_cache(maxsize=2)
def _snapshot(path, mtime_ns, size):
    """Cache only immutable source snapshots; stat keys avoid stale rebuild reads."""
    return load_jsonl(path)


def replay_task(root, task):
    shard = root / task["shard"]
    stat = shard.stat()
    resources = [
        r
        for r in _snapshot(str(shard), stat.st_mtime_ns, stat.st_size)
        if ref(r) not in task["omit"]
    ]
    store = FhirStore(
        resources,
        "Patient/" + task["patient_id"],
        task["writable_types"],
        task["delete_refs"],
        writable_refs=task.get("writable_refs", ()),
    )
    for i, step in enumerate(task["reference_steps"], 1):
        store.round = i
        result = store.request(
            step["method"], step["path"], step.get("body"), step.get("headers")
        )
        if result["status"] >= 400:
            raise ValueError(f"Reference trace failed for {task['family']}: {result}")
    final = {"answer": task["gold"]["answer"], "evidence": task["gold"]["evidence"]}
    metrics = evaluate(task, store, final)
    if metrics["strict_success"] != 1:
        raise ValueError(
            f"Reference verifier failed {task['id']} {task['family']}: {metrics}"
        )
    return {
        "task_id": task["id"],
        "family": task["family"],
        "split": task["split"],
        "metrics": metrics,
        "events": store.events,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compile and verify the FHIR Query RL search and CRUD benchmark"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--source-db")
    build.add_argument("--output", required=True)
    build.add_argument("--seed", type=int, default=17)
    build.add_argument("--pipeline", choices=["enhanced", "legacy"], default="enhanced")
    build.add_argument("--workers", type=int, default=4)
    enhance = commands.add_parser("enhance", help="Extend an immutable existing corpus")
    enhance.add_argument("--source", required=True)
    enhance.add_argument("--output", required=True)
    enhance.add_argument("--seed", type=int, default=17)
    enhance.add_argument("--workers", type=int, default=4)
    validate = commands.add_parser("validate")
    validate.add_argument("data_dir")
    validate.add_argument("--split", default="dev")
    validate.add_argument("--limit", type=int, default=0)
    validate.add_argument("--traces")
    validate.add_argument("--discovery-variants", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        from .generation import build_enhanced_dataset

        builder = (
            build_enhanced_dataset if args.pipeline == "enhanced" else build_dataset
        )
        options = {"workers": args.workers} if args.pipeline == "enhanced" else {}
        print(
            json.dumps(
                builder(args.output, args.source_db, args.seed, **options), indent=2
            )
        )
    elif args.command == "enhance":
        from .generation import enhance_dataset

        print(
            json.dumps(
                enhance_dataset(
                    args.source, args.output, args.seed, workers=args.workers
                ),
                indent=2,
            )
        )
    else:
        root = Path(args.data_dir)
        manifest = json.loads((root / "manifest.json").read_text())
        for path, digest in manifest["files"].items():
            if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Manifest checksum mismatch: {path}")
        for path in (root / args.split / "shards").glob("*.ndjson"):
            validate_graph(load_jsonl(path))
        tasks = load_jsonl(root / args.split / "tasks.jsonl")
        if args.discovery_variants:
            from .discovery import add_discovery_variants

            tasks = add_discovery_variants(
                tasks, lambda t: load_jsonl(root / t["shard"])
            )
        if args.limit:
            tasks = tasks[: args.limit]
        traces = [replay_task(root, t) for t in tasks]
        if args.traces:
            Path(args.traces).parent.mkdir(parents=True, exist_ok=True)
            with open(args.traces, "w") as f:
                for trace in traces:
                    f.write(json.dumps(trace) + "\n")
        print(
            json.dumps(
                {
                    "validated_reference_tasks": len(traces),
                    "split": args.split,
                    "strict_success": 1.0,
                    "model_rollouts": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
