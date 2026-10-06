"""Prepare the feedback-driven v0.2.4 campaign; never launch or overwrite old runs."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / "configs/revision-pilots"
folder.mkdir(parents=True, exist_ok=True)
common = [
    "latest_result",
    "coverage_lookup",
    "documented_home_medications",
    "documented_conditions",
    "longitudinal_lab_trend",
    "medication_reconciliation",
    "correct_erroneous_result",
    "delete_duplicate_draft",
]
recipes = [
    (
        "laguna-raw",
        "poolside/Laguna-XS-2.1",
        "raw",
        "native",
        common,
        0.0,
        False,
        12,
        32,
        48,
        192,
    ),
    (
        "laguna-assisted",
        "poolside/Laguna-XS-2.1",
        "assisted",
        "native",
        common,
        0.0,
        False,
        12,
        32,
        48,
        192,
    ),
    (
        "llama1b-native-retrieval",
        "sprints/Llama-3.2-1B-Instruct",
        "raw",
        "native",
        ["latest_result"],
        0.2,
        True,
        8,
        32,
        16,
        64,
    ),
    (
        "llama1b-json-retrieval",
        "sprints/Llama-3.2-1B-Instruct",
        "raw",
        "json_action",
        ["latest_result"],
        0.2,
        True,
        8,
        32,
        16,
        64,
    ),
]
for (
    name,
    model,
    profile,
    protocol,
    families,
    shaping,
    demos,
    steps,
    batch,
    cases,
    count,
) in recipes:
    args = {
        "tool_profile": profile,
        "tool_protocol": protocol,
        "query_mode": "rest",
        "max_depth": 2,
        "max_turns": 8 if "llama1b" in name else 12,
        "max_examples": count,
        "families": families,
        "answer_mode": "json_or_fence",
        "retrieval_weight": shaping,
        "demonstrations": demos,
        "efficiency_weight": 0.0,
    }

    # JSON -> TOML inline values, with structured lists/strings preserved.
    def inline(values):
        return (
            "{" + ", ".join(f"{k} = {json.dumps(v)}" for k, v in values.items()) + "}"
        )

    eva = {
        **args,
        "split": "dev",
        "eval_split": "dev",
        "max_examples": 48 if not demos else 32,
        "retrieval_weight": 0.0,
    }
    single = "\nextra_body = {parallel_tool_calls = false}" if "llama1b" in name else ""
    if "llama1b" in name and protocol == "native":
        # Environment-native sampling defaults, in addition to request overrides.
        # Nested TOML is written explicitly after the ordinary scalar arguments.
        args["sampling_args"] = {"parallel_tool_calls": False}
        eva["sampling_args"] = {"parallel_tool_calls": False}

    def with_sampling(values):
        body = {k: v for k, v in values.items() if k != "sampling_args"}
        encoded = inline(body)
        if "sampling_args" in values:
            encoded = encoded[:-1] + ", sampling_args = {parallel_tool_calls = false}}"
        return encoded

    text = f"""# Feedback-driven free campaign. Eval has no retrieval shaping.
name = "fhir-v0-2-4-{name}"
model = "{model}"
loss = "rl"
max_steps = {steps}
batch_size = {batch}
rollouts_per_example = 4
max_inflight_rollouts = {batch}
learning_rate = 0.00003

[sampling]
max_tokens = 2048
temperature = 0.7{single}

[[env]]
id = "max/fhir-workflows@0.2.4"
args = {with_sampling(args)}

[eval]
interval = 4
num_examples = {cases}
rollouts_per_example = 1
skip_first_step = false

[eval.sampling]
max_tokens = 2048
temperature = 0.0{single}

[[eval.env]]
id = "max/fhir-workflows@0.2.4"
args = {with_sampling(eva)}

[[pre_batch_filters]]
type = "zero_advantage"
enforce = false

[checkpoints]
interval = 4
keep_cloud = 2

[adapters]
interval = 4
keep_last = 2

[infrastructure]
compute_size = "M"
"""
    (folder / f"{name}.toml").write_text(text)
print(
    json.dumps(
        {
            "prepared": [str(p.relative_to(root)) for p in folder.glob("*.toml")],
            "launched": False,
        }
    )
)
