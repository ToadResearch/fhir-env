"""Small shared helpers; plans/receipts never contain credentials."""

import hashlib
import json
import os
import subprocess
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    tmp.replace(path)


def read_plan(run):
    return json.loads((Path(run) / "plan.json").read_text())


def fingerprint(repo):
    repo = Path(repo)
    files = [
        repo / p
        for p in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "environments/fhir_query_rl/pyproject.toml",
            "scripts/view_traces.py",
            "scripts/collect_hipergator_results.py",
        )
    ]
    for area in (
        "scripts/hipergator",
        "configs/hipergator",
        "environments/fhir_query_rl/fhir_query_rl",
        "environments/fhir_query_rl/fhir_locked_harness",
    ):
        files.extend(
            p
            for p in (repo / area).rglob("*")
            if p.is_file()
            and p.suffix in (".py", ".sbatch", ".sh", ".json", ".toml")
            and not any(x in p.parts for x in ("training_data", "pilot", "__pycache__"))
        )
    return {str(p.relative_to(repo)): digest(p) for p in sorted(set(files))}


def verify_code(plan):
    for name, expected in plan["code_sha256"].items():
        if digest(Path(plan["repo"]) / name) != expected:
            raise RuntimeError("Checkout changed after submission: " + name)


def call(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def load_secrets(repo):
    from dotenv import load_dotenv

    load_dotenv(Path(repo) / ".env", override=False)
    os.environ.setdefault("LOCAL_API_KEY", "local")


def eval_config(plan, port):
    return {
        "model": plan["profile"]["model"],
        "num_tasks": plan["num_examples"],
        "num_rollouts": 1,
        "max_concurrent": plan["concurrency"],
        "push": False,
        "output_dir": str(Path(plan["run_dir"]) / "evals"),
        "run": {"name": Path(plan["run_dir"]).name, "dir": plan["preset"]},
        "client": {
            "type": "eval",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key_var": "LOCAL_API_KEY",
        },
        "sampling": {**plan["profile"]["sampling"], "max_tokens": plan["max_tokens"]},
        "env": {
            "id": "fhir-query-rl",
            "taskset": {
                "id": "fhir-query-rl",
                "data_dir": plan["data_dir"],
                "split": plan["split"],
                "max_depth": plan["max_depth"],
                "max_examples": plan["num_examples"],
                "task": {
                    "query_mode": plan["query_mode"],
                    "tool_profile": plan["tool_profile"],
                    "answer_mode": "json_or_fence",
                    "efficiency_weight": 0.0,
                    "retrieval_weight": 0.0,
                },
            },
            "agent": {
                "harness": {"id": "fhir-locked-harness"},
                "runtime": {"type": "subprocess"},
                "max_turns": plan["max_turns"],
            },
        },
    }
