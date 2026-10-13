"""Prepare current eval client, vLLM SIF, model/tokenizer/data on a CPU node."""

import json
import os
import platform
import sys
import time
from pathlib import Path

from common import (
    call,
    digest,
    eval_config,
    load_secrets,
    read_plan,
    verify_code,
    write_json,
)

HOSPITAL_REV = "911f34c4ac65a508543c4b3b90c373a0cd16534d"


def container_prefix(plan, image, cache, *, gpu=False):
    # Bind only persistent inputs/outputs used by this campaign. --cleanenv
    # receives explicit APPTAINERENV_/SINGULARITYENV_ offline settings on GPU.
    roots = sorted({plan["repo"], plan["run_dir"], plan["data_dir"], str(cache)})
    argv = [plan["container_engine"], "exec", "--cleanenv"]
    if gpu:
        argv += ["--nv"]
    for root in roots:
        argv += ["--bind", root + ":" + root]
    return argv + [str(image), "/usr/bin/python3"]


def audit_data(plan):
    from fhir_query_rl.generation import build_enhanced_dataset

    root = Path(plan["data_dir"])
    source = Path(plan["source_db"])
    repo = Path(plan["repo"])
    if not root.exists():
        if not source.exists():
            destination = repo / "sources/hospital"
            if destination.exists():
                raise RuntimeError(
                    "Source folder exists but requested database is missing"
                )
            if source != destination / "benchmark_v1.3.db":
                raise RuntimeError("Custom source DB is missing: " + str(source))
            call(
                [
                    "git",
                    "clone",
                    "https://github.com/sparkcpark/synthetic_hospital.git",
                    destination,
                ]
            )
            call(["git", "-C", destination, "checkout", HOSPITAL_REV])
        build_enhanced_dataset(
            root, source_db=source, seed=17, workers=min(8, plan["cpus"])
        )
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["version"] != "0.4.0" or manifest["generation"][
        "reference_tasks_replayed"
    ] != sum(manifest["tasks"].values()):
        raise RuntimeError("Require the verified full v0.4.0 benchmark")
    for name, sha in manifest["files"].items():
        if digest(root / name) != sha:
            raise RuntimeError("Dataset checksum mismatch: " + name)
    from fhir_query_rl import load_environment

    env = load_environment(
        data_dir=str(root),
        split=plan["split"],
        query_mode=plan["query_mode"],
        tool_profile=plan["tool_profile"],
        max_depth=plan["max_depth"],
        max_examples=plan["num_examples"],
    )
    available = len(list(env.taskset))
    if available < plan["num_examples"]:
        raise RuntimeError(
            f"Requested {plan['num_examples']} tasks, but the selected cohort "
            f"contains only {available}; reduce --num-examples or change filters"
        )
    return digest(root / "manifest.json")


def main():
    run = Path(sys.argv[1]).resolve()
    plan = read_plan(run)
    repo = Path(plan["repo"])
    verify_code(plan)
    load_secrets(repo)
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Prepare the vLLM runtime on an x86_64 Linux CPU node")
    if plan["wandb_mode"] == "online" and not os.environ.get("WANDB_API_KEY"):
        raise RuntimeError("Fill WANDB_API_KEY in .env or choose --wandb-mode disabled")
    from filelock import FileLock

    started = time.perf_counter()
    Path(plan["data_dir"]).parent.mkdir(parents=True, exist_ok=True)
    with FileLock(plan["data_dir"] + ".lock"):
        data_sha = audit_data(plan)
    image = repo / ".hpg-cache/images/vllm-openai-0.31.0.sif"
    image.parent.mkdir(parents=True, exist_ok=True)
    os.environ["APPTAINER_CACHEDIR"] = str(repo / ".hpg-cache/container-cache")
    os.environ["SINGULARITY_CACHEDIR"] = os.environ["APPTAINER_CACHEDIR"]
    with FileLock(str(image) + ".lock"):
        if not image.exists():
            partial = image.with_suffix(".partial.sif")
            if partial.exists():
                partial.unlink()
            call([plan["container_engine"], "pull", partial, plan["image_uri"]])
            partial.replace(image)
    cache = (
        run / "active-model"
        if plan["delete_model_after"]
        else repo / ".hpg-cache/models"
    )
    cache.mkdir(parents=True, exist_ok=True)
    if plan["delete_model_after"]:
        write_json(cache / "owner.json", {"run": str(run)})
    os.environ["HF_HOME"] = str(cache)
    for prefix in ("APPTAINERENV_", "SINGULARITYENV_"):
        for key, value in {
            "HF_HOME": str(cache),
            "HF_MODULES_CACHE": str(cache / "modules"),
            "TIKTOKEN_CACHE_DIR": str(cache / "tiktoken"),
            "TOKENIZERS_PARALLELISM": "false",
        }.items():
            os.environ[prefix + key] = value
    from huggingface_hub import HfApi, hf_hub_download, snapshot_download

    api = HfApi()
    model_id = plan["profile"]["model"]
    revision = api.model_info(model_id, revision=plan["revision"]).sha
    model = Path(
        snapshot_download(model_id, revision=revision, cache_dir=cache / "hub")
    )
    assets = {}
    if plan["profile"].get("template_model"):
        template_id = plan["profile"]["template_model"]
        template_rev = api.model_info(template_id).sha
        template = Path(
            hf_hub_download(
                template_id,
                "chat_template.jinja",
                revision=template_rev,
                cache_dir=cache / "hub",
            )
        )
        assets["template"] = {
            "path": str(template),
            "repo_id": template_id,
            "revision": template_rev,
            "sha256": digest(template),
        }
    if plan["glimmer_dflash"]:
        assistant_id = plan["profile"]["assistant_model"]
        assistant_rev = api.model_info(assistant_id).sha
        assistant = Path(
            snapshot_download(
                assistant_id, revision=assistant_rev, cache_dir=cache / "hub"
            )
        )
        assets["assistant"] = {
            "path": str(assistant),
            "repo_id": assistant_id,
            "revision": assistant_rev,
            "files_sha256": {
                str(p.relative_to(assistant)): digest(p)
                for p in sorted(assistant.rglob("*"))
                if p.is_file()
            },
            "file_stats": {
                str(p.relative_to(assistant)): {
                    "size": p.stat().st_size,
                    "mtime_ns": p.stat().st_mtime_ns,
                }
                for p in sorted(assistant.rglob("*"))
                if p.is_file()
            },
        }
    files = {
        str(p.relative_to(model)): digest(p)
        for p in sorted(model.rglob("*"))
        if p.is_file()
    }
    stats = {
        str(p.relative_to(model)): {
            "size": p.stat().st_size,
            "mtime_ns": p.stat().st_mtime_ns,
        }
        for p in sorted(model.rglob("*"))
        if p.is_file()
    }
    write_json(
        run / "receipts/model.json",
        {
            "repo_id": model_id,
            "revision": revision,
            "path": str(model),
            "cache": str(cache),
            "files_sha256": files,
            "file_stats": stats,
            "assets": assets,
        },
    )
    prefix = container_prefix(plan, image, cache)
    probe = "from importlib.metadata import version; import json; print(json.dumps({k:version(k) for k in ('vllm','torch','transformers')}))"
    versions = json.loads(
        call(prefix + ["-c", probe], capture_output=True, text=True).stdout
    )
    if versions["vllm"] != plan["vllm_version"]:
        raise RuntimeError("Unexpected vLLM image version: " + str(versions))
    # Tokenizer + remote-code module cache and GPT-OSS Harmony are prepared offline on CPU.
    warm = """import sys
from transformers import AutoConfig, AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained(sys.argv[1],trust_remote_code=sys.argv[2]=='true',local_files_only=True)
if sys.argv[3]=='gpt-oss-20b':
    import openai_harmony
    openai_harmony.load_harmony_encoding(openai_harmony.HarmonyEncodingName.HARMONY_GPT_OSS)
if sys.argv[3]=='lfm2-5-24b-a2b':
    config = AutoConfig.from_pretrained(sys.argv[1], local_files_only=True)
    if config.model_type != 'lfm2_moe' or 'Lfm2MoeForCausalLM' not in config.architectures:
        raise RuntimeError('Unexpected LFM model architecture')
    vocab = tokenizer.get_vocab()
    if not all(t in vocab for t in ('<|tool_call_start|>', '<|tool_call_end|>')):
        raise RuntimeError('LFM tool sentinel tokens are missing')
    tools = [{'type':'function','function':{'name':'fhir_request',
        'description':'Read a synthetic FHIR resource',
        'parameters':{'type':'object','properties':{
            'method':{'type':'string'},'path':{'type':'string'}},
            'required':['method','path']}}}]
    rendered = tokenizer.apply_chat_template(
        [{'role':'user','content':'Read the FHIR server metadata.'}],
        tools=tools, tokenize=False, add_generation_prompt=True)
    tail = rendered.rsplit('<|im_start|>assistant', 1)[-1].lstrip()
    if 'fhir_request' not in rendered or not tail.startswith('<think>'):
        raise RuntimeError('LFM native tool/reasoning template did not render as expected')
"""
    call(
        prefix
        + [
            "-c",
            warm,
            model,
            str(plan["preset"] == "k2-horizon-7b").lower(),
            plan["preset"],
        ]
    )
    # Assert every serving flag exists in the current container before allocating GPUs.
    help_text = call(
        prefix + ["-m", "vllm.entrypoints.openai.api_server", "--help"],
        capture_output=True,
        text=True,
    ).stdout
    for flag in plan["profile"]["serve_args"]:
        if flag.startswith("--") and flag not in help_text:
            raise RuntimeError("Serving flag absent: " + flag)
    import tomlkit

    config = eval_config(plan, 8000)
    (run / "eval.toml").write_text(tomlkit.dumps(config))
    # A current dry-run creates its run directory. Keep that gate separate
    # so the real GPU eval can create its own run without --clean/overwrite.
    config["output_dir"] = str(run / "receipts/config-check")
    (run / "cpu-check.toml").write_text(tomlkit.dumps(config))
    call(
        [
            run / "client-venv/bin/eval",
            "@",
            run / "cpu-check.toml",
            "--dry-run",
            "--no-serve",
            "--no-rich",
            "--no-push",
        ]
    )
    verify_code(plan)
    write_json(
        run / "receipts/prepared.json",
        {
            "status": "prepared",
            "elapsed_seconds": time.perf_counter() - started,
            "data_manifest_sha256": data_sha,
            "image_path": str(image),
            "image_sha256": digest(image),
            "image_stat": {
                "size": image.stat().st_size,
                "mtime_ns": image.stat().st_mtime_ns,
            },
            "image_uri": plan["image_uri"],
            "server_versions": versions,
            "model_revision": revision,
            "eval_config_sha256": digest(run / "eval.toml"),
        },
    )


if __name__ == "__main__":
    main()
