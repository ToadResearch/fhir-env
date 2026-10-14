"""Summarize native v1 Episodes on CPU, log W&B and clean only owned model files."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import load_secrets, read_plan, write_json


def clean_owned_model(run):
    cache = run / "active-model"
    if cache.is_symlink() or cache.resolve() != cache:
        raise RuntimeError("Refusing cleanup outside the campaign-owned cache")
    if json.loads((cache / "owner.json").read_text()) != {"run": str(run)}:
        raise RuntimeError("Model ownership receipt does not match")
    shutil.rmtree(cache)


def summarize(traces):
    episodes = []
    for path in traces:
        for line in path.read_text().splitlines():
            if line.strip():
                episodes.append(json.loads(line))
    metrics = {}
    values = {}
    missing = {}
    rewards = []
    for episode in episodes:
        for trace in episode.get("traces", []):
            scored = [
                r
                for r in trace.get("rewards", {}).values()
                if isinstance(r, dict) and isinstance(r.get("score"), (int, float))
            ]
            if scored:
                rewards.append(sum(r["score"] * r.get("weight", 1) for r in scored))
            for key, value in trace.get("metrics", {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if value < 0:
                        missing[key] = missing.get(key, 0) + 1
                    else:
                        values.setdefault(key, []).append(value)
    for key, samples in values.items():
        metrics[key] = sum(samples) / len(samples)
    return {
        "episodes": len(episodes),
        "episodes_with_errors": sum(
            bool(e.get("errors")) or any(t.get("errors") for t in e.get("traces", []))
            for e in episodes
        ),
        "trace_count": sum(len(e.get("traces", [])) for e in episodes),
        "mean_metrics": metrics,
        "missing_metrics": missing,
        "mean_reward": sum(rewards) / len(rewards) if rewards else None,
    }


def main():
    run = Path(sys.argv[1]).resolve()
    plan = read_plan(run)
    receipt = {"status": "failed", "model_cleanup": "not requested"}
    gpu_path = run / "receipts/gpu.json"
    gpu = json.loads(gpu_path.read_text()) if gpu_path.exists() else {}
    receipt["gpu_status"] = gpu.get("status", "missing; inspect CPU/Slurm logs")
    jobs_path = run / "receipts/jobs.json"
    jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
    try:
        if jobs:
            result = subprocess.run(
                [
                    "sacct",
                    "-j",
                    ",".join(jobs.values()),
                    "--parsable2",
                    "--format=JobID,Account,QOS,State,ExitCode,Elapsed,AllocTRES",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            (run / "receipts/slurm-accounting.log").write_text(
                result.stdout + result.stderr
            )
        traces = sorted((run / "evals").glob("*/traces.jsonl"))
        summary = summarize(traces)
        receipt.update(summary)
        if gpu.get("status") == "completed" and summary["episodes"] != plan[
            "num_examples"
        ] * plan.get("rollouts_per_example", 1):
            raise RuntimeError("Completed evaluation has an unexpected episode count")
        if plan["wandb_mode"] == "online" and summary["episodes"]:
            load_secrets(Path(plan["repo"]))
            import wandb

            with wandb.init(
                project=os.environ.get("WANDB_PROJECT", "fhir-query-rl"),
                entity=os.environ.get("WANDB_ENTITY") or None,
                name=run.name,
                job_type="evaluation",
                config={
                    k: plan[k]
                    for k in (
                        "preset",
                        "split",
                        "tool_profile",
                        "query_mode",
                        "num_examples",
                        "rollouts_per_example",
                        "max_depth",
                    )
                },
            ) as tracker:
                tracker.log(
                    {
                        "eval/episodes": summary["episodes"],
                        "eval/episodes_with_errors": summary["episodes_with_errors"],
                        "eval/mean_reward": summary["mean_reward"],
                        **{
                            "eval/missing_" + k: v
                            for k, v in summary["missing_metrics"].items()
                        },
                        **{"eval/" + k: v for k, v in summary["mean_metrics"].items()},
                    }
                )
                receipt["wandb_url"] = tracker.url
        if plan["delete_model_after"] and (run / "active-model").exists():
            if os.environ.get("SLURM_JOB_ID") != jobs.get("finish"):
                raise RuntimeError(
                    "Owned-cache cleanup requires the dependent Slurm finalizer"
                )
            clean_owned_model(run)
            receipt["model_cleanup"] = (
                "removed owned base/assistant files; traces and manifests retained"
            )
        receipt["status"] = (
            "completed" if gpu.get("status") == "completed" else "failed"
        )
    except Exception as exc:  # noqa: BLE001 -- every reporting failure needs a receipt
        receipt["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        write_json(run / "receipts/finished.json", receipt)
        print(json.dumps(receipt, indent=2))
    sys.exit(0 if receipt["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
