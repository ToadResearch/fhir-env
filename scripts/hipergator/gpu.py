"""Offline vLLM evaluation using CPU-prepared inputs; no training or downloads."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import digest, eval_config, load_secrets, read_plan, verify_code, write_json
from prepare import container_prefix


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify_prepared(plan):
    run = Path(plan["run_dir"])
    prepared = json.loads((run / "receipts/prepared.json").read_text())
    model = json.loads((run / "receipts/model.json").read_text())
    if (
        prepared["status"] != "prepared"
        or prepared["model_revision"] != model["revision"]
    ):
        raise RuntimeError("Incomplete CPU preparation")
    if (
        digest(Path(plan["data_dir"]) / "manifest.json")
        != prepared["data_manifest_sha256"]
    ):
        raise RuntimeError("Dataset manifest changed since CPU audit")
    if digest(run / "eval.toml") != prepared["eval_config_sha256"]:
        raise RuntimeError("Evaluation config changed since CPU validation")
    image = Path(prepared["image_path"])
    stat = image.stat()
    if {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns} != prepared["image_stat"]:
        raise RuntimeError("Container changed since CPU integrity audit")
    for name, expected in model["file_stats"].items():
        stat = (Path(model["path"]) / name).stat()
        if {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns} != expected:
            raise RuntimeError("Checkpoint changed after CPU audit: " + name)
    for name, asset in model["assets"].items():
        if name == "template" and digest(asset["path"]) != asset["sha256"]:
            raise RuntimeError("Official chat template changed")
        if name == "assistant":
            for filename, expected in asset["file_stats"].items():
                stat = (Path(asset["path"]) / filename).stat()
                if {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns} != expected:
                    raise RuntimeError("DFlash checkpoint changed after CPU audit")
    return prepared, model


def runtime_environment(plan, model):
    env = os.environ.copy()
    settings = {
        "HF_HOME": model["cache"],
        "HF_MODULES_CACHE": str(Path(model["cache"]) / "modules"),
        "TIKTOKEN_CACHE_DIR": str(Path(model["cache"]) / "tiktoken"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "UV_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "1",
        "VLLM_WORKER_MULTIPROC_METHOD": "spawn",
        "VLLM_SYSTEM_START_DATE": plan["start_date"],
    }
    env.update(settings)
    # Container cleanenv needs explicit propagation, preserving Slurm's mask.
    for prefix in ("APPTAINERENV_", "SINGULARITYENV_"):
        for key, value in {
            **settings,
            "CUDA_VISIBLE_DEVICES": env["CUDA_VISIBLE_DEVICES"],
        }.items():
            env[prefix + key] = value
    return env


def serving_command(plan, prepared, model, port, rpc_port):
    argv = container_prefix(plan, prepared["image_path"], model["cache"], gpu=True)
    argv += [
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        model["path"],
        "--served-model-name",
        plan["profile"]["model"],
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--dtype",
        "bfloat16",
        "--tensor-parallel-size",
        "1",
        "--max-model-len",
        str(plan["seq_len"]),
        "--max-num-seqs",
        str(plan["profile"].get("max_num_seqs", 64)),
        "--max-num-batched-tokens",
        "8192",
        "--gpu-memory-utilization",
        "0.85",
        "--enable-prefix-caching"
        if plan["profile"].get("prefix_caching", True)
        else "--no-enable-prefix-caching",
        "--enable-chunked-prefill",
        *plan["profile"]["serve_args"],
    ]
    if plan["gpus"] > 1:
        argv += [
            "--data-parallel-size",
            str(plan["gpus"]),
            "--data-parallel-size-local",
            str(plan["gpus"]),
            "--data-parallel-rpc-port",
            str(rpc_port),
        ]
    if model["assets"].get("template"):
        argv += ["--chat-template", model["assets"]["template"]["path"]]
    if plan["glimmer_dflash"]:
        argv += [
            "--speculative-config",
            json.dumps(
                {
                    "method": "dflash",
                    "model": model["assets"]["assistant"]["path"],
                    "num_speculative_tokens": 15,
                    "draft_sample_method": "probabilistic",
                }
            ),
        ]
    return argv


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def wait_server(process, url, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("vLLM exited during startup; see logs/inference.log")
        try:
            with urllib.request.urlopen(url + "/models", timeout=5) as response:
                if json.load(response).get("data"):
                    return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(2)
    raise TimeoutError("vLLM startup exceeded 15 minutes")


def main():
    run = Path(sys.argv[1]).resolve()
    plan = read_plan(run)
    receipt = {"status": "failed", "job_id": os.environ.get("SLURM_JOB_ID")}
    started = time.perf_counter()
    processes = []
    handles = []
    exit_code = 1
    evaluation_started = None

    def interrupted(signum, frame):
        raise KeyboardInterrupt("GPU job interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        verify_code(plan)
        load_secrets(Path(plan["repo"]))
        if not os.environ.get("SLURM_JOB_ID") or not os.environ.get(
            "CUDA_VISIBLE_DEVICES"
        ):
            raise RuntimeError("Use Slurm with an assigned CUDA visibility mask")
        prepared, model = verify_prepared(plan)
        env = runtime_environment(plan, model)
        prefix = container_prefix(
            plan, prepared["image_path"], model["cache"], gpu=True
        )
        probe = """import torch,json
print(json.dumps([{'logical_index':i,'name':torch.cuda.get_device_properties(i).name,
'uuid':str(getattr(torch.cuda.get_device_properties(i),'uuid','unavailable')),
'capability':list(torch.cuda.get_device_capability(i))} for i in range(torch.cuda.device_count())]))
"""
        devices = json.loads(
            subprocess.run(
                prefix + ["-c", probe],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        if len(devices) != plan["gpus"] or any(
            d["capability"][0] != 10 for d in devices
        ):
            raise RuntimeError("Expected the requested B200 devices: " + str(devices))
        receipt.update(
            devices=devices,
            cuda_visible_devices=env["CUDA_VISIBLE_DEVICES"],
            server_versions=prepared["server_versions"],
            model_revision=model["revision"],
        )
        port = free_port()
        argv = serving_command(plan, prepared, model, port, free_port())
        receipt["server_argv"] = argv
        write_json(run / "receipts/gpu.json", receipt)
        telemetry_file = (run / "logs/gpu-utilization.csv").open("w")
        handles.append(telemetry_file)
        telemetry_argv = [
            "nvidia-smi",
            "--query-gpu=timestamp,uuid,utilization.gpu,memory.used,power.draw",
            "--format=csv",
            "-l",
            "15",
        ]
        uuids = [d["uuid"] for d in devices]
        if all(u.startswith("GPU-") for u in uuids):
            telemetry_argv += ["--id=" + ",".join(uuids)]
            receipt["telemetry_scope"] = "allocated UUIDs"
        else:
            receipt["telemetry_scope"] = "node inventory; Torch UUIDs unavailable"
        processes.append(
            subprocess.Popen(
                telemetry_argv,
                stdout=telemetry_file,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        log = (run / "logs/inference.log").open("w")
        handles.append(log)
        server = subprocess.Popen(
            argv,
            env=env,
            cwd=plan["repo"],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        processes.append(server)
        wait_server(server, f"http://127.0.0.1:{port}/v1")
        import tomlkit

        (run / "launch-eval.toml").write_text(tomlkit.dumps(eval_config(plan, port)))
        eval_argv = [
            str(run / "client-venv/bin/eval"),
            "@",
            str(run / "launch-eval.toml"),
            "--no-serve",
            "--no-rich",
            "--no-push",
        ]
        receipt["eval_argv"] = eval_argv
        eval_log = (run / "logs/evaluation.log").open("w")
        handles.append(eval_log)
        evaluation_started = time.perf_counter()
        evaluation = subprocess.Popen(
            eval_argv,
            env=env,
            cwd=plan["repo"],
            stdout=eval_log,
            stderr=eval_log,
            start_new_session=True,
        )
        processes.append(evaluation)
        exit_code = evaluation.wait()
        receipt.update(
            status="completed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            evaluation_seconds=time.perf_counter() - evaluation_started,
        )
    except BaseException as exc:
        if evaluation_started is not None:
            receipt.setdefault(
                "evaluation_seconds", time.perf_counter() - evaluation_started
            )
        receipt["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for handle in handles:
            handle.close()
        receipt["elapsed_seconds"] = time.perf_counter() - started
        write_json(run / "receipts/gpu.json", receipt)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
