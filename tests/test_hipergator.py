"""Current evaluation presets, Slurm dependencies and result integrity."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/hipergator"))
    result = {}
    for name in ("common", "launch", "prepare", "gpu", "finish"):
        spec = importlib.util.spec_from_file_location(
            "hpg_" + name, ROOT / "scripts/hipergator" / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result[name] = module
    return result


def plan(modules, tmp_path, *args):
    options = (
        modules["launch"]
        .parser()
        .parse_args(["--run-dir", str(tmp_path / "campaign"), *args])
    )
    return modules["launch"].make_plan(options, ROOT)


@pytest.mark.parametrize(
    "preset,model,parser",
    [
        ("gpt-oss-20b", "openai/gpt-oss-20b", "openai"),
        ("glimmer", "RedHatAI/Muse-Glimmer-30B-FP8-block", "muse_glimmer"),
        ("k2-horizon-7b", "IFM/K2-Horizon-7B-FP8", "k2_horizon"),
        ("lfm2-5-24b-a2b", "LiquidAI/LFM2.5-24B-A2B", "lfm2"),
    ],
)
def test_current_presets(modules, tmp_path, preset, model, parser):
    cfg = plan(modules, tmp_path, "--preset", preset)
    assert cfg["profile"]["model"] == model
    assert parser in cfg["profile"]["serve_args"]
    command = modules["gpu"].serving_command(
        cfg,
        {"image_path": "/prepared.sif"},
        {"path": "/model", "cache": "/cache", "assets": {}},
        8123,
        9123,
    )
    assert command[:3] == ["apptainer", "exec", "--cleanenv"]
    assert "--nv" in command and "--enable-auto-tool-choice" in command
    assert command[command.index("--tensor-parallel-size") + 1] == "1"
    assert "prime-rl" not in " ".join(command)
    if preset == "lfm2-5-24b-a2b":
        assert "--no-enable-prefix-caching" in command
        assert "--enable-prefix-caching" not in command
        assert command[command.index("--reasoning-parser") + 1] == "qwen3"
        assert command[command.index("--max-num-seqs") + 1] == "32"
        assert "--trust-remote-code" not in command
        assert "--chat-template" not in command


def test_cpu_gpu_resource_separation(modules, tmp_path):
    cfg = plan(modules, tmp_path)
    for phase in ("prepare", "gpu", "finish"):
        cmd = modules["launch"].command(cfg, phase)
        assert "--account=cai6734" in cmd and "--qos=cai6734" in cmd
        assert any(x.startswith("--gres=") for x in cmd) == (phase == "gpu")
    assert "--partition=hpg-b200" in modules["launch"].command(cfg, "gpu")


@pytest.mark.parametrize(
    "args",
    [
        ["--gpus", "0"],
        ["--gpus", "9"],
        ["--gpus", "8", "--cpus", "7"],
        ["--tool-profile", "none", "--query-mode", "hybrid"],
        ["--max-tokens", "65536"],
        ["--max-num-seqs", "0"],
        ["--glimmer-dflash"],
    ],
)
def test_invalid_request_rejected(modules, tmp_path, args):
    with pytest.raises(ValueError):
        plan(modules, tmp_path, *args)


def test_matched_model_capacity_and_cache_overrides(modules, tmp_path):
    commands = []
    for preset in ("gpt-oss-20b", "lfm2-5-24b-a2b"):
        cfg = plan(
            modules,
            tmp_path,
            "--preset",
            preset,
            "--max-num-seqs",
            "64",
            "--no-prefix-caching",
            "--concurrency",
            "64",
            "--num-examples",
            "320",
        )
        commands.append(
            modules["gpu"].serving_command(
                cfg,
                {"image_path": "/prepared.sif"},
                {"path": "/model", "cache": "/cache", "assets": {}},
                8123,
                9123,
            )
        )
        assert modules["common"].eval_config(cfg, 8123)["max_concurrent"] == 64
    for command in commands:
        assert command[command.index("--max-num-seqs") + 1] == "64"
        assert "--no-enable-prefix-caching" in command
        assert "--enable-prefix-caching" not in command
    cached = plan(modules, tmp_path, "--preset", "lfm2-5-24b-a2b", "--prefix-caching")
    assert cached["profile"]["prefix_caching"] is True


def test_slurm_failure_cancels_only_own_jobs(modules, tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "--test-only" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "--dependency=afterok:101" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "rejected")
        return subprocess.CompletedProcess(argv, 0, "101\n", "")

    monkeypatch.setattr(modules["launch"].subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        modules["launch"].submit(plan(modules, tmp_path))
    assert calls[-1] == ["scancel", "101"]
    assert not any(cmd[:2] == ["scontrol", "release"] for cmd in calls)


def test_complete_chain_held_until_submission_done(modules, tmp_path, monkeypatch):
    calls = []
    ids = iter(("101", "102", "103"))

    def run(argv, **kwargs):
        calls.append(argv)
        output = (
            next(ids) + "\n"
            if argv[0] == "sbatch" and "--test-only" not in argv
            else ""
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(modules["launch"].subprocess, "run", run)
    modules["launch"].submit(plan(modules, tmp_path))
    assert calls[-3:] == [
        ["scontrol", "release", "103"],
        ["scontrol", "release", "102"],
        ["scontrol", "release", "101"],
    ]
    assert "--dependency=afterok:101" in calls[4]
    assert "--dependency=afterany:102" in calls[5]


def test_native_config_schema_and_no_push(modules, tmp_path):
    import tomlkit
    from verifiers.v1.cli.resolve import narrow_config
    from verifiers.v1.configs.cli.eval import EvalConfig

    cfg = modules["common"].eval_config(plan(modules, tmp_path), 8123)
    assert tomlkit.dumps(cfg)
    config_type = narrow_config(EvalConfig, ["fhir-query-rl"])
    resolved = config_type.model_validate(cfg)
    assert resolved.push is False
    assert resolved.env.agent.runtime.type == "subprocess"
    assert resolved.env.agent.harness.id == "fhir-locked-harness"
    assert resolved.env.taskset.task.retrieval_weight == 0


def test_cpu_audit_rejects_unavailable_cohort_before_gpu(
    modules, tmp_path, monkeypatch
):
    import fhir_query_rl

    cfg = plan(modules, tmp_path, "--num-examples", "2")
    data = tmp_path / "data"
    data.mkdir()
    (data / "manifest.json").write_text(
        json.dumps(
            {
                "version": "0.4.0",
                "generation": {"reference_tasks_replayed": 1},
                "tasks": {"dev": 1},
                "files": {},
            }
        )
    )
    cfg["data_dir"] = str(data)
    from types import SimpleNamespace

    monkeypatch.setattr(
        fhir_query_rl,
        "load_environment",
        lambda **kw: SimpleNamespace(taskset=[object()]),
    )
    with pytest.raises(RuntimeError, match="Requested 2 tasks.*only 1"):
        modules["prepare"].audit_data(cfg)


def test_gpu_env_preserves_mask_and_offline(modules, tmp_path, monkeypatch):
    cfg = plan(modules, tmp_path)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-assigned-only")
    env = modules["gpu"].runtime_environment(cfg, {"cache": "/prepared/cache"})
    assert env["CUDA_VISIBLE_DEVICES"] == "GPU-assigned-only"
    assert env["APPTAINERENV_CUDA_VISIBLE_DEVICES"] == "GPU-assigned-only"
    assert env["HF_HUB_OFFLINE"] == "1" and env["UV_OFFLINE"] == "1"
    stage = (
        (ROOT / "scripts/hipergator/stage.sbatch")
        .read_text()
        .split('elif [[ "$phase" == gpu ]]; then')[1]
        .split("elif")[0]
    )
    assert "uv sync" not in stage and "uv run" not in stage


def test_summary_reports_missing_usage_and_trace_errors(modules, tmp_path):
    row = {
        "task": {},
        "traces": [
            {
                "errors": [{"message": "error"}],
                "metrics": {"strict_success": 0, "model_total_tokens": -1},
                "rewards": {"workflow": {"score": 0, "weight": 1}},
            }
        ],
    }
    path = tmp_path / "traces.jsonl"
    path.write_text(json.dumps(row) + "\n")
    summary = modules["finish"].summarize([path])
    assert summary["episodes_with_errors"] == 1
    assert summary["missing_metrics"] == {"model_total_tokens": 1}
    assert "model_total_tokens" not in summary["mean_metrics"]
    assert summary["mean_reward"] == 0


def test_owned_cleanup_rejects_symlink_and_preserves_traces(modules, tmp_path):
    cache = tmp_path / "active-model"
    cache.mkdir()
    (cache / "owner.json").write_text(json.dumps({"run": str(tmp_path)}))
    trace = tmp_path / "traces.jsonl"
    trace.write_text("{}")
    modules["finish"].clean_owned_model(tmp_path)
    assert trace.exists() and not cache.exists()
    cache.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError):
        modules["finish"].clean_owned_model(tmp_path)


def test_result_collection_paths_and_allowlist(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from collect_hipergator_results import collection_commands

    commands = collection_commands(
        "user@hpg.rc.ufl.edu", "/blue/cai6734/ehr_agent/run", "test", tmp_path
    )
    assert len(commands) == 3
    assert commands[0][-2] == "user@hpg.rc.ufl.edu:/blue/cai6734/ehr_agent/run/evals/"
    assert commands[0][-1] == str(tmp_path / "evals/test") + "/"
    assert "*.safetensors" in commands[0] and "*.sif" in commands[0]
    assert "--delete" not in commands[0]
    with pytest.raises(ValueError):
        collection_commands("user@host", "/blue/$(command)", "test", tmp_path)


def test_finalizer_cannot_claim_missing_evaluation_success(
    modules, tmp_path, monkeypatch
):
    cfg = plan(modules, tmp_path, "--wandb-mode", "disabled")
    run = Path(cfg["run_dir"])
    (run / "receipts").mkdir(parents=True)
    (run / "plan.json").write_text(json.dumps(cfg))
    (run / "receipts/gpu.json").write_text('{"status":"completed"}')
    monkeypatch.setattr(sys, "argv", ["finish.py", str(run)])
    with pytest.raises(SystemExit) as exited:
        modules["finish"].main()
    assert exited.value.code == 1
    assert (
        json.loads((run / "receipts/finished.json").read_text())["status"] == "failed"
    )
