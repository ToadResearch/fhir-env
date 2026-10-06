#!/usr/bin/env python3
"""Analyze recorded evaluation rollouts and plot actual checkpoint measurements."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from fhir_query_rl.experiments import summarize


def plots(report, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    identities = {}
    for group in report["groups"]:
        if group["stratum"] not in {"difficulty", "difficulty_crud"}:
            continue
        crud = group["category"].partition(":")[2] or "all CRUD mixes"
        identity = tuple(
            group[k]
            for k in (
                "model",
                "tool_profile",
                "query_mode",
                "seed",
                "benchmark_manifest_sha256",
            )
        ) + (crud,)
        identities.setdefault(identity, []).append(group)
    colors = {
        "easy": "#327b60",
        "medium": "#3676ae",
        "hard": "#ba8131",
        "very_hard": "#a05586",
    }
    panels = (
        ("all_rollouts", "strict_success", "Strict task success"),
        ("all_rollouts", "model_turns", "Model turns · all rollouts"),
        (
            "successful_rollouts_only",
            "model_output_tokens",
            "Output tokens · successful rollouts",
        ),
        (
            "all_rollouts",
            "all_evidence_round_observed",
            "Turns to all evidence · observed only",
        ),
    )
    for identity, groups in identities.items():
        fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
        for ax, (scope, metric, label) in zip(axes.flat, panels):
            observed = False
            for difficulty, color in colors.items():
                series = sorted(
                    [g for g in groups if g["category"].split(":")[0] == difficulty],
                    key=lambda g: g["training_step"],
                )
                # Keep missing checkpoints as gaps, never fabricate zero tokens.
                xs = [g["training_step"] for g in series]
                ys = [g[scope][metric]["mean"] for g in series]
                if any(y is not None for y in ys):
                    ax.plot(
                        xs,
                        [float("nan") if y is None else y for y in ys],
                        marker="o",
                        color=color,
                        label=difficulty,
                    )
                    cis = [g[scope][metric]["patient_ci95"] for g in series]
                    ax.fill_between(
                        xs,
                        [c[0] if c else float("nan") for c in cis],
                        [c[1] if c else float("nan") for c in cis],
                        color=color,
                        alpha=0.12,
                    )
                    observed = True
            if not observed:
                ax.text(
                    0.5,
                    0.5,
                    "No recorded measurements",
                    ha="center",
                    transform=ax.transAxes,
                )
            ax.set(xlabel="Training step", ylabel=label)
            ax.grid(alpha=0.15)
            if metric == "strict_success":
                ax.set_ylim(-0.03, 1.03)
            if observed:
                ax.legend(fontsize=8)
        model, profile, mode, seed, _, crud = identity
        fig.suptitle(
            f"{model} · {profile}/{mode} · seed {seed} · {crud}\nFixed task cohort; censored search outcomes are reported in summary.json"
        )
        name = hashlib.sha256(str(identity).encode()).hexdigest()[:12]
        fig.savefig(output / f"checkpoints-{name}.png", dpi=180)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollouts", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for path in args.rollouts
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("No rollouts supplied; no learning curves can be drawn")
    report = summarize(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    # Stable task selection across checkpoints, including failures. This is a
    # reproducible convenience selection, not a claim of representative clinical
    # prevalence; reviewers can freeze their own task list before training.
    strata = {}
    for row in rows:
        info = row["info"]
        key = (
            row["model"],
            row["seed"],
            row["benchmark_manifest_sha256"],
            info["retrieval_hops"],
            info["crud"],
        )
        strata.setdefault(key, set()).add(info["task_id"])
    chosen = {key: set(sorted(ids)[:3]) for key, ids in strata.items()}
    examples = []
    for row in rows:
        info = row["info"]
        key = (
            row["model"],
            row["seed"],
            row["benchmark_manifest_sha256"],
            info["retrieval_hops"],
            info["crud"],
        )
        if info["task_id"] in chosen[key]:
            examples.append(
                {
                    "task_id": info["task_id"],
                    "training_step": row["training_step"],
                    "model": row["model"],
                    "tool_profile": row["tool_profile"],
                    "query_mode": row["query_mode"],
                    "rollout_id": row.get("rollout_id", 0),
                    "strict_success": row.get("metrics", row.get("fhir_metrics", {}))[
                        "strict_success"
                    ],
                    "trace_available": "fhir_trace" in row,
                    "retrieval_queries": [
                        {
                            k: event[k]
                            for k in ("route", "round", "query", "status", "refs")
                        }
                        for event in row.get("fhir_trace", [])
                        if event.get("method") in {"GET", "SELECT"}
                    ],
                }
            )
    (args.output / "same-task-queries.json").write_text(
        json.dumps(examples, indent=2) + "\n"
    )
    with (args.output / "summary.csv").open("w") as stream:
        fields = [
            "model",
            "tool_profile",
            "query_mode",
            "seed",
            "training_step",
            "stratum",
            "category",
            "scope",
            "metric",
            "observed",
            "missing",
            "mean",
            "median",
            "patient_ci95",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for g in report["groups"]:
            for scope in ("all_rollouts", "successful_rollouts_only"):
                for metric, stats in g[scope].items():
                    writer.writerow(
                        {
                            **{k: g[k] for k in fields[:7]},
                            "scope": scope,
                            "metric": metric,
                            **stats,
                        }
                    )
    if args.plots:
        plots(report, args.output)
    print(
        json.dumps(
            {
                "rollouts": report["rollouts"],
                "groups": len(report["groups"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
