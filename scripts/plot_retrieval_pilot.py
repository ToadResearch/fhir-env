"""Plot the actual small Llama dev curriculum, without claiming generalization."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
snapshot = root / "artifacts/training/revision-snapshots/20261002T150440Z"
run_id = "tc11uaw9gk1jzczv36ysweku"
metrics = json.loads((snapshot / run_id / "metrics.json").read_text())["metrics"]
prefix = "eval/max/fhir-workflows@0.2.4/"
rows = []
for metric in sorted(metrics, key=lambda m: m["step"]):
    if metric.get(prefix + "avg@1") is not None:
        rows.append(
            {
                "orchestrator_step": metric["step"],
                "policy_version": metric[prefix + "policy_version"],
                "dev_cases": 16,
                "successes": round(metric[prefix + "avg@1"] * 16),
                "workflow_success": metric[prefix + "avg@1"],
                "truncation_fraction": metric[prefix + "is_truncated/mean"],
            }
        )
output = root / "artifacts/training/llama-json-retrieval-learning"
output.with_suffix(".json").write_text(
    json.dumps(
        {
            "run_id": run_id,
            "source_snapshot": str(snapshot.relative_to(root)),
            "evaluation_shaping": 0.0,
            "family": "latest_result",
            "rows": rows,
            "limits": "16 dev cases from 32-case tuning pool, one authored task template, fictional demonstrations, no heldout result. Orchestrator step differs from policy version. Not evidence of general CRUD capability or search efficiency.",
        },
        indent=2,
    )
    + "\n"
)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12})
fig, ax = plt.subplots(figsize=(8, 5.2), layout="constrained")
fig.set_facecolor("#fafaf9")
ax.set_facecolor("#fafaf9")
versions = [r["policy_version"] for r in rows]
scores = [r["workflow_success"] for r in rows]
ax.plot(versions, scores, color="#176b79", linewidth=2.7, marker="o", markersize=8)
for row in rows:
    ax.annotate(
        f"{row['successes']}/16",
        (row["policy_version"], row["workflow_success"]),
        xytext=(0, 12),
        textcoords="offset points",
        ha="center",
        fontweight="bold",
    )
ax.set(
    ylim=(-0.08, 1.14),
    xlim=(-0.4, 6.4),
    xticks=versions,
    yticks=[0, 0.25, 0.5, 0.75, 1],
    xlabel="Recorded policy version",
    ylabel="Dev workflow success",
)
ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
ax.grid(axis="y", color="#dededb", linewidth=0.8)
ax.spines[["top", "right"]].set_visible(False)
ax.set_title(
    "Llama 1B · JSON-action retrieval curriculum", loc="left", pad=18, fontweight="bold"
)
fig.supxlabel(
    "16 cases per eval from a 32-case dev pool · no evaluation shaping\nTuning result; general CRUD capability and efficiency remain unmeasured.",
    fontsize=10,
)
fig.savefig(output.with_suffix(".png"), dpi=170)
fig.savefig(output.with_suffix(".svg"))
print(output.with_suffix(".png"))
