"""Submit CPU preparation -> B200 evaluation -> CPU reporting; no training."""

import argparse
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

from common import fingerprint, write_json


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--preset",
        choices=["gpt-oss-20b", "glimmer", "k2-horizon-7b", "lfm2-5-24b-a2b"],
        default="gpt-oss-20b",
    )
    p.add_argument(
        "--revision",
        default="main",
        help="Resolve current HF revision to an immutable commit on CPU",
    )
    p.add_argument(
        "--glimmer-dflash",
        action="store_true",
        help="Optional official Meta DFlash assistant; Glimmer only",
    )
    p.add_argument("--run-dir", type=Path)
    p.add_argument(
        "--data-dir", type=Path, default=Path("data/synthetic-hospital-v0.4.0")
    )
    p.add_argument(
        "--source-db", type=Path, default=Path("sources/hospital/benchmark_v1.3.db")
    )
    p.add_argument("--account", default=os.environ.get("HPG_ACCOUNT", "cai6734"))
    p.add_argument("--qos", default=os.environ.get("HPG_QOS", "cai6734"))
    p.add_argument("--cpu-partition")
    p.add_argument("--gpus", type=int, default=1)
    p.add_argument("--cpus", type=int, default=8)
    p.add_argument("--memory", default="64G")
    p.add_argument("--cpu-time", default="04:00:00")
    p.add_argument("--gpu-time", default="02:00:00")
    p.add_argument("--num-examples", type=int, default=32)
    p.add_argument("--concurrency", type=int, default=32)
    p.add_argument(
        "--max-num-seqs",
        type=int,
        help="Override per-replica server capacity for matched throughput comparisons",
    )
    p.add_argument(
        "--prefix-caching",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the model preset's prefix caching policy",
    )
    p.add_argument("--split", choices=["dev", "public", "heldout"], default="dev")
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--tool-profile", choices=["raw", "assisted", "none"], default="raw")
    p.add_argument("--query-mode", choices=["rest", "hybrid"], default="rest")
    p.add_argument("--seq-len", type=int, default=65536)
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--wandb-mode", choices=["online", "disabled"], default="online")
    p.add_argument("--delete-model-after", action="store_true")
    p.add_argument(
        "--container-engine", default="apptainer", choices=["apptainer", "singularity"]
    )
    p.add_argument("--dry-run", action="store_true")
    return p


def make_plan(args, repo):
    if not 1 <= args.gpus <= 8 or args.cpus < args.gpus:
        raise ValueError("Request 1..8 B200s and at least one CPU per GPU")
    if (
        min(
            args.num_examples,
            args.concurrency,
            args.max_depth,
            args.max_turns,
            args.max_tokens,
        )
        < 1
    ):
        raise ValueError(
            "Task, concurrency, depth, turn and token limits must be positive"
        )
    if args.max_tokens >= args.seq_len:
        raise ValueError("Completion budget must be smaller than context length")
    if args.max_num_seqs is not None and args.max_num_seqs < 1:
        raise ValueError("Server sequence capacity must be positive")
    if args.tool_profile == "none" and args.query_mode != "rest":
        raise ValueError("The supplied-chart control uses rest mode, without tools")
    if args.glimmer_dflash and args.preset != "glimmer":
        raise ValueError("DFlash is available only for the Glimmer preset")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in (args.account, args.qos)):
        raise ValueError("Account/QoS must be simple scheduler names")
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%SZ")  # noqa: UP017 -- login-node Python 3.9
    run = (
        args.run_dir or repo / "outputs/hipergator" / f"{args.preset}-{timestamp}"
    ).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run.name):
        raise ValueError("Campaign directory must have a simple name")
    plan = {
        k: str(v) if isinstance(v, Path) else v
        for k, v in vars(args).items()
        if k != "dry_run"
    }
    profiles = json.loads((repo / "configs/hipergator/models.json").read_text())
    profile = profiles[args.preset]
    if args.max_num_seqs is not None:
        profile["max_num_seqs"] = args.max_num_seqs
    if args.prefix_caching is not None:
        profile["prefix_caching"] = args.prefix_caching
    plan.update(
        repo=str(repo),
        run_dir=str(run),
        data_dir=str((repo / args.data_dir).resolve()),
        source_db=str((repo / args.source_db).resolve()),
        profile=profile,
        image_uri="docker://vllm/vllm-openai:v0.31.0",
        vllm_version="0.31.0",
        start_date=datetime.datetime.now(datetime.timezone.utc).date().isoformat(),  # noqa: UP017 -- login-node Python 3.9
        code_sha256=fingerprint(repo),
    )
    return plan


def command(plan, phase):
    run = Path(plan["run_dir"])
    argv = [
        "sbatch",
        "--parsable",
        "--account=" + plan["account"],
        "--qos=" + plan["qos"],
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=" + str(plan["cpus"]),
        "--mem=" + plan["memory"],
        "--chdir=" + plan["repo"],
        "--job-name=fhir-" + phase,
        "--output=" + str(run / "logs" / f"{phase}-%j.log"),
    ]
    if phase == "gpu":
        argv += [
            "--partition=hpg-b200",
            "--gres=gpu:b200:" + str(plan["gpus"]),
            "--time=" + plan["gpu_time"],
            "--kill-on-invalid-dep=yes",
        ]
    else:
        argv += ["--time=" + ("00:30:00" if phase == "finish" else plan["cpu_time"])]
        if plan["cpu_partition"]:
            argv += ["--partition=" + plan["cpu_partition"]]
    return argv


def submit(plan):
    run = Path(plan["run_dir"])
    run.mkdir(parents=True, exist_ok=False)
    for folder in ("logs", "receipts", "evals"):
        (run / folder).mkdir()
    write_json(run / "plan.json", plan)
    submitted = []
    receipts = []
    try:
        for phase in ("prepare", "gpu", "finish"):
            argv = command(plan, phase) + [
                "--test-only",
                str(Path(plan["repo"]) / "scripts/hipergator/stage.sbatch"),
                phase,
                str(run),
                plan["repo"],
            ]
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
            receipts.append(
                {
                    "phase": phase,
                    "preflight": True,
                    "argv": argv,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                }
            )
            write_json(run / "receipts/submission.json", receipts)
            result.check_returncode()
        for phase in ("prepare", "gpu", "finish"):
            argv = command(plan, phase) + ["--hold"]
            if phase == "gpu":
                argv += ["--dependency=afterok:" + submitted[0]]
            if phase == "finish":
                argv += ["--dependency=afterany:" + submitted[1]]
            argv += [
                str(Path(plan["repo"]) / "scripts/hipergator/stage.sbatch"),
                phase,
                str(run),
                plan["repo"],
            ]
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
            receipts.append(
                {
                    "phase": phase,
                    "argv": argv,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                }
            )
            write_json(run / "receipts/submission.json", receipts)
            result.check_returncode()
            job = result.stdout.strip().split(";")[0]
            if not job.isdigit():
                raise RuntimeError("Unrecognized sbatch job ID: " + result.stdout)
            submitted.append(job)
        write_json(
            run / "receipts/jobs.json",
            {"prepare": submitted[0], "gpu": submitted[1], "finish": submitted[2]},
        )
        for job in reversed(submitted):
            subprocess.run(["scontrol", "release", job], check=True)
    except BaseException:
        for job in submitted:
            subprocess.run(["scancel", job], check=False)
        raise
    print(json.dumps({"run_dir": str(run), "jobs": submitted}, indent=2))


def main():
    args = parser().parse_args()
    repo = Path(__file__).resolve().parents[2]
    plan = make_plan(args, repo)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "plan": plan,
                    "sbatch": [command(plan, p) for p in ("prepare", "gpu", "finish")],
                },
                indent=2,
            )
        )
    else:
        submit(plan)


if __name__ == "__main__":
    main()
