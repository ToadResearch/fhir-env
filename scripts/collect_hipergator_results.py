"""Collect native v1 eval traces and Slurm receipts; never models or environments."""

import argparse
import re
import subprocess
from pathlib import Path

EXCLUDED = (
    ".*",
    "*secret*",
    "*credential*",
    "*api_key*",
    "*api-key*",
    "weights",
    "checkpoints",
    "models",
    "model",
    "cache",
    "runtime",
    "client-venv",
    "*.safetensors",
    "*.pt",
    "*.bin",
    "*.sif",
    "__pycache__",
)
DATA_PATTERNS = ("*.json", "*.jsonl", "*.csv", "*.log", "*.out", "*.err", "*.toml")


def collection_commands(host, remote_base, campaign, output_dir, *, dry_run=False):
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.-]+)?", host
    ) or host.startswith("-"):
        raise ValueError("host must be an SSH alias or user@hostname")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", campaign) or campaign in (
        ".",
        "..",
    ):
        raise ValueError("campaign must be a single directory name")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote_base) or ".." in remote_base.split(
        "/"
    ):
        raise ValueError(
            "remote output base must be an absolute path without shell characters"
        )
    base = remote_base.rstrip("/")
    output = Path(output_dir).resolve()
    commands = []
    for folder, destination in [
        ("evals", output / "evals" / campaign),
        ("logs", output / "hipergator" / campaign / "logs"),
        ("receipts", output / "hipergator" / campaign / "receipts"),
    ]:
        argv = ["rsync", "-rt", "--safe-links", "--prune-empty-dirs"]
        if dry_run:
            argv += ["--dry-run", "--itemize-changes"]
        for pattern in EXCLUDED:
            argv += ["--exclude", pattern]
        argv += ["--include", "*/"]
        for pattern in DATA_PATTERNS:
            argv += ["--include", pattern]
        argv += [
            "--exclude",
            "*",
            "--",
            f"{host}:{base}/{folder}/",
            str(destination) + "/",
        ]
        commands.append(argv)
    return commands


def collect(host, remote_base, campaign, output_dir, *, dry_run=False, view=False):
    for argv in collection_commands(
        host, remote_base, campaign, output_dir, dry_run=dry_run
    ):
        Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        subprocess.run(argv, check=True)
    if view and not dry_run:
        repo = Path(__file__).resolve().parents[1]
        subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(repo / "scripts/view_traces.py"),
                "--outputs-dir",
                str(Path(output_dir).resolve() / "evals"),
            ],
            cwd=repo,
            check=True,
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--remote-output-base", required=True)
    p.add_argument("--campaign", required=True)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--view", action="store_true")
    args = p.parse_args()
    collect(
        args.host,
        args.remote_output_base,
        args.campaign,
        args.output_dir,
        dry_run=args.dry_run,
        view=args.view,
    )


if __name__ == "__main__":
    main()
