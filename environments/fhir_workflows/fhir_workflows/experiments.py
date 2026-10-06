"""Offline checkpoint analysis. This module never launches inference or training."""

from collections import Counter, defaultdict
import math
import random
import statistics

from .metrics import rollout_usage_metrics


METRICS = (
    "strict_success",
    "workflow_success",
    "answer_success",
    "state_success",
    "evidence_recall",
    "evidence_precision",
    "model_turns",
    "model_input_tokens",
    "model_output_tokens",
    "model_total_tokens",
    "token_usage_complete",
    "token_usage_call_coverage",
    "first_evidence_round_observed",
    "all_evidence_round_observed",
    "first_evidence_applicable",
    "first_evidence_censored",
    "all_evidence_censored",
    "tool_calls",
    "agent_tool_calls",
    "primitive_operations",
    "response_bytes",
    "helper_response_bytes",
    "tool_errors",
    "unsafe_attempts",
    "first_action_targeted_search",
    "first_action_broad_search",
    "first_action_document_read",
    "first_turn_targeted_search",
    "first_search_gold_resource_hit",
    "first_search_required_fields_hit",
    "first_turn_gold_resource_hit",
    "first_turn_required_fields_hit",
    "search_calls",
    "unique_resources_per_search_mean",
    "backend_resources_per_search_mean",
    "repeated_queries",
    "distinct_filter_terms",
    "novel_filter_terms",
    "filter_novelty_observed",
    "query_characters_mean",
    "query_predicate_count_mean",
    "query_parameter_count_mean",
    "query_chain_depth_mean",
    "query_includes_mean",
    "query_joins_mean",
)


def finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def normalize(rows):
    """Validate provenance and fixed task cohorts before drawing learning curves.

    Each record is one rollout, with an explicit checkpoint and a task's fixed
    dimensions. A manifest digest identifies the immutable base corpus. Variant
    versions and exact task IDs are checked through the task cohort as well.
    """
    normalized, annotations, cohorts, duplicate_keys = (
        [],
        {},
        defaultdict(Counter),
        set(),
    )
    controls = {}
    for row in rows:
        row = dict(row)
        info = row["info"]
        step = row["training_step"]
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise ValueError("training_step must be an explicit nonnegative integer")
        for key in (
            "model",
            "tool_profile",
            "query_mode",
            "seed",
            "benchmark_manifest_sha256",
        ):
            if key not in row or row[key] is None:
                raise ValueError(f"Missing experiment identity: {key}")
        digest = row["benchmark_manifest_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("benchmark_manifest_sha256 must be a SHA-256 hex digest")
        if row["tool_profile"] not in {"raw", "assisted", "none"} or row[
            "query_mode"
        ] not in {"rest", "hybrid"}:
            raise ValueError("Unknown tool profile or query mode")
        config = row.get("evaluation_config", {})
        required_controls = {
            "environment_version",
            "max_calls",
            "max_turns",
            "max_completion_tokens",
            "tool_protocol",
            "answer_mode",
            "efficiency_weight",
            "retrieval_weight",
            "discovery_variants",
        }
        if required_controls - config.keys():
            raise ValueError(
                "Missing evaluation_config with frozen budgets and verifier settings"
            )
        control_key = (row["model"], row["seed"], digest, row["tool_profile"] == "none")
        if control_key in controls and controls[control_key] != config:
            raise ValueError(
                "Evaluation budgets, interface or verifier settings changed across compared runs"
            )
        controls[control_key] = config
        if info["split"] not in {"dev", "public", "heldout"}:
            raise ValueError(
                "Checkpoint analysis accepts evaluation splits, not training cases"
            )
        hops = info["retrieval_hops"]
        if not isinstance(hops, int) or isinstance(hops, bool) or hops < 1:
            raise ValueError("retrieval_hops must be a fixed positive integer")
        dimension = tuple(
            info[k] for k in ("patient_id", "family", "split", "retrieval_hops", "crud")
        )
        task_key = (digest, info["task_id"])
        if task_key in annotations and annotations[task_key] != dimension:
            raise ValueError(
                "Task dimensions changed across checkpoints or access profiles"
            )
        annotations[task_key] = dimension
        row["info"] = dict(
            info,
            difficulty_bin="easy"
            if hops == 1
            else "medium"
            if hops == 2
            else "hard"
            if hops == 3
            else "very_hard",
        )
        experiment = tuple(
            row[k]
            for k in (
                "model",
                "tool_profile",
                "query_mode",
                "seed",
                "benchmark_manifest_sha256",
            )
        )
        key = (experiment, step, info["task_id"], row.get("rollout_id", 0))
        if key in duplicate_keys:
            raise ValueError(
                "Duplicate rollout; repeated samples need distinct rollout_id values"
            )
        duplicate_keys.add(key)
        cohorts[experiment + (step,)][info["task_id"]] += 1
        metrics = dict(row.get("metrics", row.get("fhir_metrics", {})))
        if "trajectory" in row:
            metrics.update(rollout_usage_metrics(row["trajectory"]))
        if metrics.get("strict_success") not in (0, 1):
            raise ValueError("Each rollout must include strict_success=0 or 1")
        # Never infer missing usage from response lengths, characters or bytes.
        if metrics.get("token_usage_complete") != 1:
            for name in (
                "model_input_tokens",
                "model_output_tokens",
                "model_total_tokens",
            ):
                metrics[name] = -1
        row["metrics"] = metrics
        normalized.append(row)
    by_experiment = defaultdict(list)
    for identity, cohort in cohorts.items():
        by_experiment[identity[:-1]].append(cohort)
    for values in by_experiment.values():
        if any(v != values[0] for v in values[1:]):
            raise ValueError(
                "Evaluation cohort or rollouts per task changed across checkpoints"
            )
    # Compare assistance/SQL only on genuinely paired tasks. The supplied-chart
    # control is intentionally a smaller read-only cohort and stays separate.
    paired = defaultdict(list)
    for identity, values in by_experiment.items():
        model, profile, _, seed, digest = identity
        if profile != "none":
            paired[(model, seed, digest)].append(values[0])
    for values in paired.values():
        if any(v != values[0] for v in values[1:]):
            raise ValueError(
                "Raw/assisted/SQL comparison requires identical evaluation cohorts"
            )
    return normalized


def statistic(rows, metric, resamples=400):
    values = [(r["info"]["patient_id"], r["metrics"].get(metric)) for r in rows]
    values = [(p, v) for p, v in values if finite(v)]
    if not values:
        return {
            "observed": 0,
            "missing": len(rows),
            "mean": None,
            "median": None,
            "patient_ci95": None,
        }
    patients = defaultdict(list)
    for patient, value in values:
        patients[patient].append(value)
    ci = None
    if len(patients) >= 2 and resamples:
        rng = random.Random(17)
        clusters = list(patients.values())
        means = []
        for _ in range(resamples):
            sample = [
                v for cluster in rng.choices(clusters, k=len(clusters)) for v in cluster
            ]
            means.append(statistics.mean(sample))
        means.sort()
        ci = [
            means[int(0.025 * (len(means) - 1))],
            means[int(0.975 * (len(means) - 1))],
        ]
    return {
        "observed": len(values),
        "missing": len(rows) - len(values),
        "mean": statistics.mean(v for _, v in values),
        "median": statistics.median(v for _, v in values),
        "patient_ci95": ci,
    }


def summarize(rows, resamples=400):
    rows = normalize(rows)
    groups = defaultdict(list)
    for row in rows:
        identity = tuple(
            row[k]
            for k in (
                "model",
                "tool_profile",
                "query_mode",
                "seed",
                "benchmark_manifest_sha256",
                "training_step",
            )
        )
        info = row["info"]
        for level, category in (
            ("difficulty", info["difficulty_bin"]),
            ("difficulty_crud", info["difficulty_bin"] + ":" + info["crud"]),
            ("family", info["family"]),
        ):
            groups[identity + (level, category)].append(row)
    output = []
    for identity, group in sorted(groups.items(), key=lambda pair: str(pair[0])):
        model, profile, query_mode, seed, digest, step, level, category = identity
        successful = [r for r in group if r["metrics"]["strict_success"] == 1]
        output.append(
            {
                "model": model,
                "tool_profile": profile,
                "query_mode": query_mode,
                "seed": seed,
                "benchmark_manifest_sha256": digest,
                "training_step": step,
                "stratum": level,
                "category": category,
                "rollouts": len(group),
                "evaluation_config": group[0]["evaluation_config"],
                "patients": len({r["info"]["patient_id"] for r in group}),
                "successful_rollouts": len(successful),
                "all_rollouts": {m: statistic(group, m, resamples) for m in METRICS},
                "successful_rollouts_only": {
                    m: statistic(successful, m, resamples) for m in METRICS
                },
            }
        )
    return {
        "rollouts": len(rows),
        "groups": output,
        "interpretation": "Task-weighted summaries; patient-cluster bootstrap intervals. Missing/censored values excluded from conditional means and counted separately. These metrics alone do not establish emergent or linear scaling.",
    }
