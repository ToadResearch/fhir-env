# Liquid LFM2.5 for FHIR query RL

Research snapshot: **13 October 2026**. LFM2.5-24B-A2B is a promising model to
benchmark. The repository now has an **experimental evaluation preset**; its
exact tokenizer, serving loop and B200 performance still need cluster execution.
The training integration below is a proposal, not an implemented trainer.
See [GPT-OSS versus Liquid](gpt-oss-vs-liquid.md) for published B200 references,
the current absence of a matched new-model comparison, and training support.

## Model and throughput

Start with the post-trained
[`LiquidAI/LFM2.5-24B-A2B`](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B),
rather than the separately published Base checkpoint. The model card reports
24B total parameters, 2.45B active per token, a convolution/attention MoE
architecture and 131,072-token context. It generates reasoning before its answer.
[Inspected checkpoint](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/1769516ac588c4c558089c94f27d96b0dc67b183/README.md).

Liquid reports over 21K output tokens/sec on **one B300**, using BF16,
1,024-token inputs, up to 256 output tokens and sustained high concurrency with
benchmark-reported SGLang 0.5.22. This is a useful serving result, but does not
establish B200 throughput or complete FHIR episodes/sec. Our growing tool
transcripts, reasoning length, CPU tool execution and optimizer work change the
workload. [Launch methodology](https://www.liquid.ai/blog/lfm2-5-24b-a2b).

All weights remain resident: 24B BF16 parameters alone imply roughly **48 GB**.
From the checkpoint's 10 attention layers, 8 KV heads and 64-dimensional heads,
the BF16 attention KV payload is approximately 20 KiB/token: 640 MiB for a 32K
sequence, before padding, convolution checkpoints, workspaces or fragmentation.
These are derived estimates, not measured memory usage.
[Configuration](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/1769516ac588c4c558089c94f27d96b0dc67b183/config.json).

## Official recipes worth reading

| Recipe | Actual model and scope | What transfers to this project |
|---|---|---|
| [Liquid GRPO for verifiable tasks](https://github.com/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_for_verifiable_tasks.ipynb) · [Colab](https://colab.research.google.com/github/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_for_verifiable_tasks.ipynb) | LFM2.5-1.2B-Instruct; BF16 LoRA; custom Python rewards returning floats | Minimal GRPO recipe; replace math rewards with our episode verifier |
| [Liquid GRPO with Unsloth](https://github.com/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_with_unsloth.ipynb) · [Colab](https://colab.research.google.com/github/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_with_unsloth.ipynb) | 1.2B-Instruct; LoRA and graded reward examples | Reward-function mechanics and efficient small-model experiments |
| [Liquid IFStruct GRPO](https://github.com/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_with_trl_ifstruct.ipynb) · [Colab](https://colab.research.google.com/github/Liquid4All/cookbook/blob/main/finetuning/notebooks/grpo_with_trl_ifstruct.ipynb) | LFM2.5-350M; weighted JSON/schema/field-count rewards | A concrete continuous-reward example; explicitly measures schema compliance, not semantic correctness |
| [Liquid BrowserGym/OpenEnv example](https://github.com/Liquid4All/cookbook/tree/f9ee38dd89462335691170ac647ebd30fc96a038/examples/browser-control) | LFM2-350M; custom multi-turn rollouts and terminal environment rewards | Closest conceptual match to executing FHIR tools and checking resulting state |

These are smaller-model examples, not verified 24B GRPO recipes. Some contain
older trainer APIs or generic Llama LoRA targets; copy the design and validate the
current implementation rather than changing only the model ID.

## Current training support

| Route | Finding from executable source | Implication |
|---|---|---|
| Liquid's training runner | At `d9cc23dae5a16498ab7dc11958b80ca803e0440a`, [`grpo_run`](https://github.com/Liquid4All/liquid-finetune/blob/d9cc23dae5a16498ab7dc11958b80ca803e0440a/src/liquid_finetune/training/grpo.py#L85) explicitly rejects MoE models | Its SFT/DPO support and GRPO defaults do not provide working MoE GRPO |
| Its OpenEnv adapter | The supplied [adapter](https://github.com/Liquid4All/liquid-finetune/blob/d9cc23dae5a16498ab7dc11958b80ca803e0440a/src/liquid_finetune/rl/environments/adapter.py) restricts its built-in path to one turn and text actions | Full FHIR CRUD needs a custom rollout loop |
| Current PrimeRL | At `4487ef3d7d3ecf14da7a51af2444844cc86ad72b`, the [trainer](https://github.com/PrimeIntellect-ai/prime-rl/blob/4487ef3d7d3ecf14da7a51af2444844cc86ad72b/src/prime_rl/trainer/model.py#L409) requires a registered PrimeRL model implementation; `lfm2_moe` is absent | Adding a model ID or remote-code flag cannot enable training; an architecture port is required |
| Direct current TRL | [TRL 1.15.0](https://github.com/huggingface/trl/releases/tag/v1.15.0) exposes custom rollouts, environment masks and LFM2.5 response parsing | Best candidate for a small integration prototype; exact 24B gradients and adapter synchronization remain untested |

Use a **separate locked training environment**, keeping the working evaluation
workspace intact. A candidate stack to resolve and smoke-test is TRL 1.15.0 with
Transformers 5.19.0, PEFT 0.21.2 and a compatible vLLM stack. This is not a tested
dependency lock. Liquid's current runner pins an older
[stack](https://github.com/Liquid4All/liquid-finetune/blob/d9cc23dae5a16498ab7dc11958b80ca803e0440a/pyproject.toml);
it should not silently replace the root client or GPU container.

## How the FHIR training bridge should work

Our task is naturally **RL with verifiable rewards**: GRPO can use our
programmatic checker without collecting human preference labels or training a
separate reward model.

```text
training-split task + frozen chart
             |
      fresh store per rollout
             |
policy --> native tool call --> existing FHIR tools/store
   ^                                  |
   +---------- tool observation ------+
             |
     final answer + evidence + mutation audit
             |
     existing evaluate_training checker --> terminal reward
             |
sampled token IDs + old logprobs + environment mask --> GRPO update
             |
     same frozen dev evaluation and trace viewer
```

Reuse task definitions, `FhirStore`, tools and `evaluate_training`; do not fork
their clinical or mutation semantics. Create an independent store for each
generation in a GRPO group. A completion-only answer checker cannot verify CRUD.

The custom rollout must retain exact sampled tokens and their behavior-policy
log probabilities. Tool responses and inserted scaffolding receive a zero loss
mask; sampled model tokens receive one. Preserve reasoning and native tool-call
tokens through the training transcript. Current
[TRL GRPO](https://huggingface.co/docs/trl/v1.15.0/en/grpo_trainer) supports custom
rollouts; its [source](https://github.com/huggingface/trl/blob/dae7b6adc8d2729cfa8d21bc8480656ede769240/trl/trainer/grpo_trainer.py)
handles `env_mask` for this purpose.
Assert that each next prompt preserves the previous sampled token history as an
exact prefix; a template that strips earlier reasoning breaks that contract.
For an initial custom-loop prototype, let the loop own tool execution and pass
no separate trainer tools/environment, preventing duplicate execution. Keep
sampling transforms neutral for the first logprob-parity test, then tune
exploration after that test passes.

Keep strict task completion as the baseline reward: supported answer, required
evidence access and exactly authorized chart changes. Existing bounded retrieval
shaping can be a later ablation. Schema validity or selecting the right tool is
insufficient reward for a completed workflow. Track group reward variance; if
every sample fails, use easier training tasks or a small verified-action SFT
warmup before increasing difficulty. Keep dev/public/heldout out of that warmup.

First validate one optimizer update with BF16 LoRA and HF generation, avoiding
rollout-server synchronization initially. Start with attention/convolution
projection adapters and verify nonzero gradients, frozen base weights, finite
loss and checkpoint reload. Fused expert weights require
[`target_parameters`](https://huggingface.co/docs/peft/v0.21.0/package_reference/lora#targeting-nnparameter-directly),
not merely `all-linear` or dense `w1/w2/w3` module targets. Confirm expert-adapter
export and serving parity before enabling them.

Next test synchronized vLLM rollouts on a separate GPU. A proposed initial layout
is one training B200 plus one rollout B200, subject to measured memory. After an
update, verify changed policy outputs, matching token/logprob calculations and
cache invalidation. Scale trainer/rollout allocation using measured step time;
eight rollout replicas are not automatically the best training layout.

## Experimental evaluation preset

The preset uses our pinned vLLM 0.31.0, BF16, TP1, 32 server sequences, native
`lfm2` tools and `qwen3` reasoning parsing. It preserves the checkpoint's own
template and disables prefix caching initially. Native
[model support](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/model_executor/models/lfm2_moe.py)
and [tool parsing](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/tool_parsers/lfm2_tool_parser.py)
are present; exact-model execution remains a gate. Sampling starts from the
model card's temperature 0.1, top-k 50 and repetition penalty 1.05. This is an
evaluation setting, not a recommended GRPO exploration temperature.

```bash
# Inspect locally; no downloads or Slurm submissions.
bash scripts/hipergator/submit.sh --preset lfm2-5-24b-a2b \
  --seq-len 32768 --dry-run

# On HiPerGator: CPU preparation -> one B200 -> CPU reporting.
bash scripts/hipergator/submit.sh --preset lfm2-5-24b-a2b \
  --seq-len 32768 --num-examples 32 --concurrency 32 \
  --run-dir outputs/hipergator/lfm25-dev-01
```

CPU preparation checks architecture, tool sentinel vocabulary and the native
`<think>` generation prefix before GPU execution. The GPU smoke must establish
actual parser behavior, tool results returned to the model, bounded reasoning
and clean episode isolation. The existing pipeline resolves an immutable model
revision and records input hashes. It does not start training.

Then sweep concurrency 1/8/32/64 on matched tasks and repeat with longer
transcripts. Record strict success, successful episodes/GPU-hour, TTFT, time to
first tool, reasoning/output tokens, truncation, parser errors, CPU tool time and
peak GPU memory. Retain the difficulty/CRUD breakdown from the
[evaluation protocol](metrics-and-evaluation.md).

Test hybrid prefix caching separately with `mamba_cache_mode=align` and chunked
prefill, verifying cache hits, repeated-prefix correctness and interleaved-request
isolation. Avoid older recipes' `all` mode: the
[v0.31 hybrid configuration](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/model_executor/models/config.py)
falls back to align for this architecture. FP8, expert LoRA and speculative
decoding are later experiments, not assumptions behind the baseline.

The model's [LFM Open License](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/main/LICENSE)
governs weights and derivatives; it is separate from training-code licenses.
Review its terms before eventual model publication or commercial deployment.
