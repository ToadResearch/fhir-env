# Evaluate on HiPerGator

The root **uv workspace** installs current stable Prime 0.9.3 and **native Verifiers
v1** (package 0.3.1), W&B and our offline trace viewer on Python 3.13. The GPU server
uses the official **vLLM 0.31.0** container. There are no legacy API overrides,
PrimeRL runtime patches or training launches in this setup.

```text
submit.sh                 Slurm account AND QoS: cai6734
    |
CPU prepare               uv sync; verify/build charts; prepare vLLM SIF
    |                     cache models/tokenizers/templates; pin and hash inputs
    | afterok
B200 evaluate             offline HF; current native tools; traces + metrics
    |                     one TP1 replica/GPU, no training or downloads
    | afterany
CPU finish                summarize episodes; W&B; Slurm outcome; owned cleanup
    |
collect results           outputs/evals/CAMPAIGN/RUN/traces.jsonl
    |
view_traces.py             browse questions, replies, tools, evidence, metrics
```

## Setup

For Slurm evaluation, **do not create or activate a virtual environment first**.
Run `bash scripts/hipergator/submit.sh` from the checkout on the login node. The
wrapper uses system Python and the standard library to submit jobs; dependency
installation and downloads happen in the CPU preparation job.

Each campaign gets its own environment at
`outputs/hipergator/CAMPAIGN/client-venv`. The CPU stage sets
`UV_PROJECT_ENVIRONMENT` to that path and runs `uv sync --locked --python 3.13
--no-dev`. GPU evaluation and CPU reporting reuse its interpreter. A root
`.venv` is optional for local development and is not used by the Slurm jobs.

If `.env` does not exist yet, create it once from the repository root:

```bash
cp .env.example .env
chmod 600 .env
```

Keep an existing `.env`; copying the example again would overwrite its values.
Fill `WANDB_API_KEY` and optionally `WANDB_ENTITY`/`HF_TOKEN`. Scripts load `.env`.
Use `--wandb-mode disabled` if you want an entirely local pilot without W&B.
No Prime-platform key, evaluation upload or Environment Hub publication is needed.

For local development or browsing collected traces outside Slurm, `uv sync
--locked` creates the root `.venv`; use `uv run` without needing to activate it.

On HiPerGator, put the checkout on persistent shared storage, for example
`/blue/cai6734/ehr_agent/fhir-env`. Install uv >=0.11.1 and make Apptainer (or
Singularity) available. The submission wrapper needs system Python >=3.9;
the CPU stage provisions the locked Python 3.13 client. Check the allocation:

```bash
showAssoc "$USER"
slurminfo -g cai6734 -u
```

Both account and QoS must allow `cai6734`. Historical `cai5724` quotas in the
supplied lessons are not assumed. GPU jobs explicitly request `hpg-b200` with
`--gres=gpu:b200:N`; CPU jobs request no GPU.
[UF GPU scheduling rules](https://docs.rc.ufl.edu/scheduler/gpu_access/) require at
least one CPU per requested GPU.

## Model presets

| Preset | Checkpoint | Native parsers |
|---|---|---|
| `gpt-oss-20b` | [`openai/gpt-oss-20b`](https://huggingface.co/openai/gpt-oss-20b) | OpenAI tools; built-in Harmony reasoning |
| `glimmer` | [`RedHatAI/Muse-Glimmer-30B-FP8-block`](https://huggingface.co/RedHatAI/Muse-Glimmer-30B-FP8-block) | `muse_glimmer`; official Meta chat template |
| `k2-horizon-7b` | [`IFM/K2-Horizon-7B-FP8`](https://huggingface.co/IFM/K2-Horizon-7B-FP8) | `k2_horizon`; publisher remote tokenizer code |
| `lfm2-5-24b-a2b` (experimental) | [`LiquidAI/LFM2.5-24B-A2B`](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B) | `lfm2` tools; `qwen3` reasoning; native checkpoint template |

Quantization comes from checkpoint metadata. Each starts on **one B200**, with
64K context, 16K maximum completion tokens per model call, TP1,
chunked prefill and 8K batched-prefill tokens. Model-specific sampling/reasoning
settings are recorded in [models.json](../configs/hipergator/models.json).
The original presets enable prefix caching. The Liquid preset starts with 32
server sequences and prefix caching disabled.
Its [research and smoke-test guide](liquid-lfm2-5.md) recommends a 32K context
pilot and explains the separate training work still needed. It has not run on
B200s yet; the four-model command below includes it as an experimental baseline.

Glimmer defaults to ordinary decoding. `--glimmer-dflash` adds the official
`meta-models/Muse-Glimmer-30B-assistant` with 15 speculative tokens, following the
successful DFlash recipe in your supplied lessons. DSpark's historical failures
are not used as a launch recipe. Both target and optional assistant revisions,
template hash and effective server arguments are saved.

## Launch evaluation

Run **on HiPerGator** from the repository root:

```bash
# Inspect only: no submissions, directories or downloads.
bash scripts/hipergator/submit.sh --preset gpt-oss-20b --dry-run

# Start with 32 dev tasks; defaults are 1 B200, 8 CPUs and 64G host RAM.
bash scripts/hipergator/submit.sh --preset gpt-oss-20b \
  --run-dir outputs/hipergator/gptoss-dev-01

bash scripts/hipergator/submit.sh --preset glimmer \
  --run-dir outputs/hipergator/glimmer-dev-01

bash scripts/hipergator/submit.sh --preset k2-horizon-7b \
  --run-dir outputs/hipergator/k2-dev-01
```

Run sequentially to establish the allocation and serving gates first. The
launcher preflights all three stages, submits a held dependency chain, then
releases it. Submission failure cancels only that invocation's jobs. CPU failure
prevents GPU execution; the dependent finalizer records failures too.

CPU preparation builds missing v0.4.0 data from the pinned Synthetic Hospital
source, or verifies existing checksums. It caches the SIF and checkpoint, resolves
`--revision main` to the current immutable HF commit, warms tokenizers/Harmony,
and validates the current eval configuration. GPU jobs use those local paths with
HF offline mode and preserve Slurm's CUDA mask. Startup/compilation still consume
GPU allocation time; model/container downloads and dataset generation do not.
Requests exceeding the available filtered cohort fail during CPU preparation.
The local `fhir-locked-harness` plugin executes Verifiers' unchanged null tool
loop with the CPU-installed interpreter. It avoids the upstream script bootstrap
installation path during GPU rollouts; no upstream source is patched.

### Run GPT-OSS and Liquid on the same tasks

Copy or push the current code to HiPerGator before submitting. From that checkout,
this starts one B200 evaluation for each model on the same 320 dev tasks:

```bash
for preset in gpt-oss-20b lfm2-5-24b-a2b; do
  bash scripts/hipergator/submit.sh --preset "$preset" \
    --account cai6734 --qos cai6734 --gpus 1 \
    --num-examples 320 --concurrency 32 --max-num-seqs 64 \
    --seq-len 32768 --max-tokens 8192 --no-prefix-caching \
    --gpu-time 04:00:00 --wandb-mode disabled
done
```

Use `--num-examples 32 --concurrency 8 --max-num-seqs 32` for an initial smoke
test. Add `--dry-run` to preview without submitting. W&B is disabled in these
examples; omit `--wandb-mode disabled` after filling its API key in `.env`.
Each invocation prints its output directory and prepare/GPU/finish job IDs.
Submitting both can allocate two B200s at once, subject to available quota.
The first real jobs validate serving and the exact model parsers; local tests
have not established B200 success or performance. Keep the checkout unchanged
while jobs are queued/running because they verify source hashes.

### Run all four models on the same tasks

From the repository root on HiPerGator, submit four evaluation chains. A shared
batch label makes their output directories easy to identify:

```bash
batch="four-model-dev-$(date -u +%Y%m%d-%H%M%SZ)"
for preset in gpt-oss-20b glimmer k2-horizon-7b lfm2-5-24b-a2b; do
  bash scripts/hipergator/submit.sh --preset "$preset" \
    --run-dir "outputs/hipergator/${batch}-${preset}" \
    --account cai6734 --qos cai6734 --gpus 1 \
    --split dev --num-examples 320 \
    --tool-profile raw --query-mode rest \
    --concurrency 32 --max-num-seqs 64 \
    --seq-len 32768 --max-tokens 8192 --no-prefix-caching \
    --gpu-time 04:00:00 --wandb-mode disabled || break
done
```

Each model evaluates the same selected 320 dev tasks with the same access and
budgets; model-specific sampling and native parsers remain in its preset.
This is the raw FHIR baseline, not the helper/SQL ablation. Each chain requests
one B200 (up to four concurrently, subject to quota), 8 CPUs and 64G host RAM.
No manual virtual environment is required. CPU preparation creates each
campaign's `client-venv`; dependencies, charts and models are prepared there.
Remove `--wandb-mode disabled` to enable W&B after filling its key in `.env`.
Use 32 tasks, concurrency 8 and server capacity 32 for initial smoke tests;
add `--dry-run` to preview instead of submitting. If one submission fails,
the loop stops; earlier successful submissions remain queued.

For larger matched cohorts, use the same split, count and settings for every
preset. Dev has 2,986 base tasks. Public/heldout contain separate patient cohorts;
freeze the design before using heldout. Example scaling command:

```bash
bash scripts/hipergator/submit.sh --preset gpt-oss-20b \
  --num-examples 2986 --gpus 4 --cpus 16 --memory 128G \
  --concurrency 128 --gpu-time 12:00:00 \
  --run-dir outputs/hipergator/gptoss-dev-full-01
```

`--gpus` accepts 1–8 and enables independent TP1 data-parallel replicas; the
multi-GPU layout remains a cluster validation gate. Select resources/time from
measured smoke results and account limits. Use `--tool-profile assisted` for the
helper ablation, `--query-mode hybrid` for SQL retrieval, `--max-depth` for
difficulty subsets and `--split public|heldout` for later frozen evaluation. The
`none` control is supplied-chart, read-only evaluation, with no query tools.

For matched throughput tests, `--max-num-seqs N` overrides per-replica server
capacity and `--prefix-caching` / `--no-prefix-caching` overrides the preset's
cache policy. These are separate from client `--concurrency`. See the
[GPT-OSS/Liquid comparison](gpt-oss-vs-liquid.md) for published measurements and
paired dry-run commands. GPU receipts separate evaluation time from total GPU
stage time, including startup and shutdown.

## Inspect results locally

The standard cluster output root is **`outputs/hipergator/`**, with one directory
per campaign/model. The four-model command names each directory
`four-model-dev-TIMESTAMP-PRESET`. Collected Mac traces use **`outputs/evals/`**;
both roots are Git-ignored. Keep campaign directories intact so traces remain
paired with their effective configuration, hashes and receipts.

```text
outputs/hipergator/CAMPAIGN/
  plan.json                 request, preset, code hashes
  eval.toml                 CPU-validated config
  launch-eval.toml           config with allocated server port
  evals/PRESET/
    traces.jsonl            native v1 Episodes and their full traces
    configs/resolved/
      eval.json             effective evaluation configuration
  logs/                     Slurm, inference, evaluation, utilization
  receipts/                 versions, input hashes, job IDs, outcomes, W&B URL
  client-venv/              CPU-provisioned client environment; not collected
```

The current Prime CLI **no longer includes the old local evaluation TUI**. This
project provides a new offline viewer for current native traces, without keeping
an old Prime version. It shows questions, final replies, the recorded message
graph, tool calls, FHIR evidence, metrics and errors. The current Verifiers eval
command also has a live Rich dashboard for interactive runs; batch jobs use logs.
[Current CLI source](https://github.com/PrimeIntellect-ai/prime/tree/v0.9.3/packages/prime/src/prime_cli/commands)
and [native v1 evaluation source](https://github.com/PrimeIntellect-ai/verifiers/tree/v0.3.1/verifiers/v1/cli/eval)
define these surfaces. Evaluation uploads are explicitly disabled.

The viewer discovers native results directly from `outputs/hipergator/` (or
`outputs/`), as well as collected results from `outputs/evals/`. It scans campaign
evaluation directories without traversing their environments or model caches.
After CPU preparation completes, open it on HiPerGator using any prepared
campaign's interpreter; replace `CAMPAIGN` with its actual directory name:

```bash
outputs/hipergator/CAMPAIGN/client-venv/bin/python scripts/view_traces.py \
  --outputs-dir outputs/hipergator
```

This is a lightweight offline terminal UI, not GPU inference. Restart it to
reload traces written since it opened. Use `r` to focus the run list, `t` to
focus tasks, arrow keys to navigate and `q` to quit.

On your Mac:

```bash
uv run python scripts/collect_hipergator_results.py \
  --host YOUR_USERNAME@hpg.rc.ufl.edu \
  --remote-output-base /blue/cai6734/ehr_agent/fhir-env/outputs/hipergator/gptoss-dev-01 \
  --campaign gptoss-dev-01 --view

# Open collected results again:
uv run python scripts/view_traces.py --outputs-dir outputs/evals
```

Collection preserves the native run/config hierarchy and copies only traces,
metrics, logs and receipts. `--dry-run` previews transfers. Models, containers,
environments and credentials are excluded. All generated outputs/caches are
Git-ignored. W&B receives CPU-aggregated evaluation metrics; complete traces stay
local for inspection.

## Cache retention and validation

Models are reused in `.hpg-cache/models/` and the SIF in `.hpg-cache/images/`.
`--delete-model-after` uses a campaign-owned model cache and deletes only that
cache in the dependent CPU finalizer; traces, revisions and hashes stay. Shared
caches are never purged. No adapters are produced because these are evaluations.

Current stable versions are pinned in `uv.lock` and the container preset for
reproducibility, without legacy overrides. The
[vLLM 0.31.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.31.0),
[GPT-OSS recipe](https://recipes.vllm.ai/openai/gpt-oss-20b) and
[Glimmer recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B) support the
serving choices. The supplied measurements used vLLM 0.30.0; they guide sizing,
not claims of performance on the new runtime.

Native API integration and offline viewer tests run locally. The actual Linux
CPU preparation and B200 smoke evaluation remain the next gates. No HiPerGator
jobs or model-weight downloads were started while implementing this setup.
