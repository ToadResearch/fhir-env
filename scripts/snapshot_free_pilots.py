"""Save read-only Prime pilot status, metrics, logs and available rollouts."""

import argparse
import concurrent.futures
import datetime as dt
import json
import subprocess
from pathlib import Path


def prime(arguments):
    result = subprocess.run(
        ["prime", "--plain", "train", *arguments],
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def capture(run, output):
    folder = output / run["id"]
    folder.mkdir(parents=True, exist_ok=True)
    errors = {}
    collected = {}
    queries = {
        "run": ["get", run["id"], "--output", "json"],
        "progress": ["progress", run["id"]],
        "metrics": ["metrics", run["id"], "--limit", "100"],
        "usage": ["usage", run["id"], "--output", "json"],
    }
    for name, arguments in queries.items():
        try:
            value = json.loads(prime(arguments))
            (folder / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
            collected[name] = value
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors[name] = str(exc)
    step = collected.get("progress", {}).get("latest_step")
    sampled_step = None
    if step is not None:
        try:
            samples = json.loads(
                prime(["rollouts", run["id"], "--step", str(step), "--num", "20"])
            )
            (folder / f"rollouts-step{step}.json").write_text(
                json.dumps(samples, indent=2) + "\n"
            )
            sampled_step = step
            if not samples.get("samples") and step > 0:
                samples = json.loads(
                    prime(
                        [
                            "rollouts",
                            run["id"],
                            "--step",
                            str(step - 1),
                            "--num",
                            "20",
                        ]
                    )
                )
                sampled_step = step - 1
                (folder / f"rollouts-step{sampled_step}.json").write_text(
                    json.dumps(samples, indent=2) + "\n"
                )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            errors["rollouts"] = str(exc)
    try:
        (folder / "orchestrator.log").write_text(
            prime(["logs", run["id"], "--tail", "80", "--raw"])
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        errors["logs"] = str(exc)
    metrics = collected.get("metrics", {}).get("metrics", [])
    metrics = sorted(metrics, key=lambda m: m.get("step", -1))
    baseline = next(
        (
            m
            for m in metrics
            if any(
                k.startswith("eval/") and "avg@1" in k and v is not None
                for k, v in m.items()
            )
        ),
        {},
    )
    latest = metrics[-1] if metrics else {}
    latest_dev = next(
        (
            m
            for m in reversed(metrics)
            if any(
                k.startswith("eval/") and k.endswith("/avg@1") and v is not None
                for k, v in m.items()
            )
        ),
        {},
    )
    observed = collected.get("run", {}).get("run", {})
    usage = collected.get("usage", {})
    observed_names = {
        "strict_success",
        "canonical_success",
        "workflow_success",
        "reward",
        "workflow_reward",
        "tool_calls",
        "agent_tool_calls",
        "retrieval_progress",
        "action_format_errors",
        "first_evidence_call_observed",
        "all_evidence_call_observed",
        "first_evidence_censored",
        "all_evidence_censored",
        "json_format_valid",
    }
    return {
        "id": run["id"],
        "name": run["name"],
        "model": run["model"],
        "profile": run["profile"],
        "protocol": run.get("protocol", "native"),
        "url": run["url"],
        "status": observed.get("status"),
        "error_message": observed.get("error_message"),
        "latest_step": step,
        "sampled_step": sampled_step,
        "training_tokens": usage.get("training", {}).get("tokens"),
        "total_cost_usd": usage.get("total_cost_usd"),
        "baseline": {
            k: v
            for k, v in baseline.items()
            if k.startswith("eval/")
            and any(x in k for x in ("avg@1", "errored_count", "is_truncated/mean"))
        },
        "latest_dev": {
            k: v
            for k, v in latest_dev.items()
            if k == "step"
            or (
                k.startswith("eval/")
                and any(
                    x in k
                    for x in (
                        "avg@1",
                        "policy_version",
                        "errored_count",
                        "is_truncated/mean",
                    )
                )
            )
        },
        "latest_recorded": {
            k: v
            for k, v in latest.items()
            if k == "step"
            or k.rsplit("/", 1)[-1] in observed_names
            or (k.endswith("/mean") and k.rsplit("/", 2)[-2] in observed_names)
        },
        "capture_errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("artifacts/training/pilot-runs.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/training/snapshots")
    )
    options = parser.parse_args()
    runs = json.loads(options.manifest.read_text())["runs"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = options.output / stamp
    output.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda run: capture(run, output), runs))
    result = {"captured_at_utc": stamp, "runs": results}
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"snapshot": str(output), **result}, indent=2))


if __name__ == "__main__":
    main()
