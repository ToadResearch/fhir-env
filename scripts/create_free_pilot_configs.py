"""Create bounded matched hosted pilots; this script never launches a run."""

import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", choices=["llama1b", "laguna"], action="append")
parser.add_argument("--environment-version", default="0.2.3")
options = parser.parse_args()
output = root / "configs/free-pilots"
output.mkdir(parents=True, exist_ok=True)
families = [
    "latest_result",
    "coverage_lookup",
    "documented_home_medications",
    "documented_conditions",
    "longitudinal_lab_trend",
    "medication_reconciliation",
    "correct_erroneous_result",
    "delete_duplicate_draft",
]
read_only = ["latest_result", "coverage_lookup", "longitudinal_lab_trend"]
models = {
    "llama1b": "sprints/Llama-3.2-1B-Instruct",
    "laguna": "poolside/Laguna-XS-2.1",
}
created = []
for label, model in models.items():
    if options.model and label not in options.model:
        continue
    for profile in ("raw", "assisted", "none"):
        selected = read_only if profile == "none" else families
        steps = 6 if profile == "none" else 12
        config = f"""# Free-model infrastructure/learning pilot. No efficiency reward.
name = "fhir-v{options.environment_version.replace('.', '-')}-{label}-{profile}"
model = "{model}"
loss = "rl"
max_steps = {steps}
batch_size = 32
rollouts_per_example = 4
max_inflight_rollouts = 32
learning_rate = 0.00003

[sampling]
max_tokens = 1024
temperature = 0.7

[[env]]
id = "max/fhir-workflows@{options.environment_version}"
args = {{tool_profile = "{profile}", query_mode = "rest", max_depth = 2, max_turns = 12, max_examples = 192, families = {json.dumps(selected)}, efficiency_weight = 0.0}}

[eval]
interval = 6
num_examples = 12
rollouts_per_example = 1
skip_first_step = false

[eval.sampling]
max_tokens = 1024
temperature = 0.0

[[eval.env]]
id = "max/fhir-workflows@{options.environment_version}"
args = {{split = "dev", eval_split = "dev", tool_profile = "{profile}", query_mode = "rest", max_depth = 2, max_turns = 12, max_examples = 48, families = {json.dumps(selected)}, efficiency_weight = 0.0}}

# Disable only the pre-batch zero-advantage filter for diagnostics. The hosted
# post-batch filter still drops untrainable groups and can abort all-zero runs.
[[pre_batch_filters]]
type = "zero_advantage"
enforce = false

[checkpoints]
interval = 6
keep_cloud = 2

[adapters]
interval = 6
keep_last = 2

[infrastructure]
compute_size = "M"
"""
        path = output / f"{label}-{profile}.toml"
        path.write_text(config)
        created.append(str(path.relative_to(root)))
print(json.dumps({"configs": created, "launched": False}))
