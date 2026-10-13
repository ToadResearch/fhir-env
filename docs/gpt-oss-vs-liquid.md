# GPT-OSS 20B versus Liquid LFM2.5

Research snapshot: **13 October 2026**. Both are worth benchmarking. **GPT-OSS
requires less new training infrastructure for the first PrimeRL experiment**, because its
architecture and Harmony renderer are implemented upstream. This is a support
assessment, not a measured FHIR accuracy or throughput ranking.

## Published throughput

No matched B200/B300 comparison of **LFM2.5-24B-A2B versus GPT-OSS-20B** was found
in the primary sources checked. The following measurements are separate
references; do not divide their headline values to claim a speedup.

### GPT-OSS: measured B200 reference

NVIDIA reports these **output tokens/sec/GPU** on B200 180GB, TP1, with weights
labeled FP4, using TensorRT-LLM **1.3.0rc9**:

| Input tokens | Output tokens | Output tokens/sec/GPU |
|---:|---:|---:|
| 1,024 | 1,024 | 53,491 |
| 1,024 | 8,192 | 34,702 |
| 8,192 | 1,024 | 11,904 |
| 32,768 | 1,024 | 2,645 |

These are saturated offline runs with rapidly queued random-token requests,
FP8 KV cache and CUTLASS MoE. The version is a prerelease. The table does not
identify an immutable checkpoint revision or call its FP4 weights NVFP4.
This is neither our vLLM baseline nor a BF16 training measurement.
[NVIDIA table and reproduction method](https://nvidia.github.io/TensorRT-LLM/1.3.0rc9/developer-guide/perf-overview.html#gpt-oss-20b).

### Liquid: measured B300 reference

Liquid's new chart reports **aggregate output tokens/sec on one B300**:

| Concurrent requests | LFM2.5-24B-A2B output tokens/sec |
|---:|---:|
| 1 | 341.5 |
| 32 | 3,794 |
| 64 | 6,131 |
| 1,024 | 21,070 |

Its embedded chart specifies **SGLang 0.5.22.dev109**, BF16, 1,024 input / up to
256 output tokens, sustained replacement of completed requests, and **one
measured run per level**. It includes Qwen, Nemotron and Gemma comparators,
not GPT-OSS. No raw-run download, uncertainty bands or exact per-model launch
commands are supplied. [Liquid launch and chart](https://www.liquid.ai/blog/lfm2-5-24b-a2b).

### Older direct comparison

Liquid's February launch did compare **LFM2-24B-A2B** with GPT-OSS-20B on one
H100 SXM5: 26.8K versus 16.0K **total tokens/sec**, at concurrency 1,024 and up
to 1,024 input / 512 output tokens. GPU precision and vLLM version are unspecified.
This concerns the predecessor, a different GPU and unspecified token accounting; it cannot
rank the new model on B200. [Historical chart](https://www.liquid.ai/blog/lfm2-24b-a2b).

The public [Pipette results](https://pipette.liquid.ai/results) and
[submissions](https://pipette.liquid.ai/submissions) did not expose a matched
new-24B/GPT-OSS pair in the inspected model filters. Its usual device methodology
is separate from the launch GPU benchmark.

## Serving versus training

| Question | GPT-OSS 20B | LFM2.5-24B-A2B |
|---|---|---|
| Total / active parameters | About 21B / 3.6B | 24B / 2.45B |
| Existing evaluation checkpoint | `openai/gpt-oss-20b`, native MXFP4 experts | `LiquidAI/LFM2.5-24B-A2B`, BF16 |
| Context | 131,072 tokens | 131,072 tokens |
| Current PrimeRL architecture | Implemented `gpt_oss` | No `lfm2_moe` implementation |
| First training route to validate | Native PrimeRL BF16 LoRA | Custom current TRL bridge |

Model specifications: [OpenAI documentation](https://developers.openai.com/api/docs/models/gpt-oss-20b),
[Liquid checkpoint](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/1769516ac588c4c558089c94f27d96b0dc67b183/README.md).

The existing GPT preset keeps native checkpoint quantization. Our server's
`--dtype bfloat16` does **not** dequantize its MXFP4 experts. OpenAI estimates
approximately 16GB with MXFP4 versus 48GB with BF16; long-context caches and
workspaces need additional memory.
[OpenAI serving guidance](https://developers.openai.com/cookbook/articles/gpt-oss/run-transformers#pick-your-model).

Current PrimeRL requires a **BF16 checkpoint** such as
`unsloth/gpt-oss-20b-BF16`; original MXFP4 checkpoints are unsupported for its
trainer. It uses FlashAttention 4 learned attention sinks on SM90/SM100/SM110,
including B200 hardware. Its native port has an upstream 20-step RL smoke run on
eight H200s using reverse-text tasks, not FHIR.
[Pinned training requirements](https://github.com/PrimeIntellect-ai/prime-rl/blob/4487ef3d7d3ecf14da7a51af2444844cc86ad72b/docs/advanced.md#L35),
[implementation validation](https://github.com/PrimeIntellect-ai/prime-rl/pull/3424).

Thus native quantized-serving throughput does not forecast BF16 RL rollout or
optimizer speed. Benchmark both precision paths before estimating training
duration. Record actual storage/compute/reduction dtypes; a BF16 checkpoint does
not imply that optimizer state or all persistent tensors are BF16.

## Exact-model training references

| Reference | Verified scope |
|---|---|
| [OpenAI/Hugging Face fine-tuning guide](https://developers.openai.com/cookbook/articles/gpt-oss/fine-tune-transfomers) | Exact 20B SFT; dequantizes to BF16; LoRA including expert parameters; designed for one H100 80GB |
| [Unsloth 20B BF16 GRPO notebook](https://github.com/unslothai/notebooks/blob/26a618143bb36ecb36050b1bcb38b602b64a2ee5/nb/gpt_oss_%2820B%29_GRPO_BF16.ipynb) · [Colab](https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/gpt_oss_%2820B%29_GRPO_BF16.ipynb) | Exact BF16 mirror and executable kernel correctness/speed rewards; useful custom-reward example |
| [Unsloth/OpenEnv 2048 notebook](https://github.com/unslothai/notebooks/blob/26a618143bb36ecb36050b1bcb38b602b64a2ee5/nb/OpenEnv_gpt_oss_%2820B%29_Reinforcement_Learning_2048_Game_BF16.ipynb) | Stateful reward example; generates strategy code, rather than directly implementing our FHIR tool loop |

Our FHIR taskset, MCP tools and `workflow_reward` already use native Verifiers v1.
Reuse their chart isolation and exact mutation/evidence checks. The remaining
work is a current trainer integration and smoke test, rather than a new task
definition or model architecture.

Use a separate training lock/container: current PrimeRL requires Python 3.12,
newer Verifiers and different Transformers dependencies from our evaluation
workspace. The environment package's exact Verifiers 0.3.1 pin also needs a
deliberate tested update for that runtime; isolation alone does not resolve it.
Do not restore the retired HF fallback or introduce legacy API overrides.
[Pinned dependencies](https://github.com/PrimeIntellect-ai/prime-rl/blob/4487ef3d7d3ecf14da7a51af2444844cc86ad72b/pyproject.toml).

The BF16 mirror's name is not recognized by the renderer's automatic exact-name
map. A future training config should explicitly select Harmony:

```toml
[orchestrator.renderer]
name = "gpt-oss"
auto_drop_analysis = false
```

Retain the native renderer's sampled-token bridge; complete re-rendering can
rewrite history even with analysis retained. Verify observation masks,
round-trip token IDs, nonzero gradients, one adapter update and save/reload.
Prime expert LoRA requires rank divisible by eight, so a notebook's rank four
cannot be copied unchanged. LoRA synchronization uses filesystem adapters rather
than NCCL broadcast. These are integration requirements, not a completed recipe.
[Harmony renderer](https://github.com/PrimeIntellect-ai/renderers/blob/41f9290949043a23fefe90a887b83b5b1ef685c8/renderers/gpt_oss.py),
[expert adapters](https://github.com/PrimeIntellect-ai/prime-rl/blob/4487ef3d7d3ecf14da7a51af2444844cc86ad72b/src/prime_rl/trainer/models/layers/lora/multi_moe.py#L588).

## Matched FHIR benchmark

Start with one B200 per model, evaluated sequentially, and at least 320 frozen
dev tasks. Compare concurrency 1/8/32/64 with three timing repetitions, common
context/output budgets, task order, charts, tool profile and verifier. Keep the
native model templates and record model-specific sampling. Add GPT low/medium
reasoning effort as a separate ablation; Liquid's checkpoint is reasoning-only.

```bash
# Preview both requests; this submits nothing.
for preset in gpt-oss-20b lfm2-5-24b-a2b; do
  bash scripts/hipergator/submit.sh --preset "$preset" \
    --num-examples 320 --concurrency 32 --max-num-seqs 64 \
    --seq-len 32768 --max-tokens 8192 --no-prefix-caching \
    --run-dir "outputs/hipergator/${preset}-matched-c32-r1" --dry-run
done
```

The new `--max-num-seqs` and `--[no-]prefix-caching` overrides address the original
64-versus-32 server-capacity difference. Client concurrency alone cannot bypass
server capacity, and 32 total examples cannot exercise 64 simultaneous episodes.
First compare both with caching off. A deployment-tuning phase can then compare
GPT caching with validated Liquid align caching. At native precision this is a
deployment comparison; a BF16/BF16 training rollout comparison is separate.

Rank by **strict success and successful episodes/GPU-hour**, alongside p50/p95
episode latency, turns, tokens, bytes, truncations and parser errors by hop/CRUD
group. Different tokenizers make tokens/sec alone insufficient. Divide aggregate
generated tokens by shared evaluation wall time, not the sum of overlapping
episode durations. Include reasoning tokens in output usage exactly once.

The GPU receipt now separates `evaluation_seconds` from allocation-stage
`elapsed_seconds`, which includes startup and shutdown. Existing utilization
samples and native trace timings remain available. TTFT, first-tool latency,
tool execution timing, engine metrics and performance aggregation by phase
still need instrumentation; the finalizer currently averages task metrics.
Report GPU cost including startup separately from evaluation throughput.

No GPU measurements, training runs or model downloads were performed for this
research. GPT-OSS is already available as `--preset gpt-oss-20b`; the Liquid
[integration note](liquid-lfm2-5.md) records its additional training work.
