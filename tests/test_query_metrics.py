import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from verifiers.types import Response, ResponseMessage, Usage

from fhir_query_rl.cli import replay_task
from fhir_query_rl.dataset import load_jsonl
from fhir_query_rl.discovery import FAMILIES, discovery_variant
from fhir_query_rl.experiments import normalize, summarize
from fhir_query_rl.fhir_query_rl import load_environment
from fhir_query_rl.helpers import chart_timeline
from fhir_query_rl.metrics import (
    filter_terms,
    rollout_usage_metrics,
    search_behavior,
    task_dimensions,
)
from fhir_query_rl.sql import query
from fhir_query_rl.store import FhirStore
from fhir_query_rl.validation import ref


PILOT = (
    Path(__file__).resolve().parents[1]
    / "environments/fhir_query_rl/fhir_query_rl/pilot"
)


def episode(family="latest_result"):
    task = next(
        t for t in load_jsonl(PILOT / "dev/tasks.jsonl") if t["family"] == family
    )
    resources = [
        r for r in load_jsonl(PILOT / task["shard"]) if ref(r) not in task["omit"]
    ]
    store = FhirStore(
        resources,
        "Patient/" + task["patient_id"],
        task["writable_types"],
        task["delete_refs"],
    )
    store.round = 1
    return task, store


def test_usage_counts_actual_calls_and_does_not_double_count_reasoning():
    response = Response(
        id="test",
        created=0,
        model="test",
        message=ResponseMessage(
            content="done", finish_reason="stop", is_truncated=False
        ),
        usage=Usage(
            prompt_tokens=100,
            completion_tokens=40,
            reasoning_tokens=30,
            total_tokens=140,
        ),
    )
    trajectory = [
        {"response": response},
        {"response": {"usage": {"prompt_tokens": 150, "completion_tokens": 5}}},
    ]
    metrics = rollout_usage_metrics(trajectory)
    assert metrics["model_turns"] == 2
    assert metrics["model_input_tokens"] == 250 and metrics["model_output_tokens"] == 45
    assert metrics["model_total_tokens"] == 295 and metrics["token_usage_complete"] == 1
    trajectory.append({"response": {}})
    metrics = rollout_usage_metrics(trajectory)
    assert metrics["model_total_tokens"] == -1
    assert (
        metrics["observed_model_output_tokens"] == 45
        and metrics["token_usage_call_coverage"] == 2 / 3
    )
    assert rollout_usage_metrics([])["model_total_tokens"] == -1


def test_filters_exclude_transport_controls_and_sql_json_paths():
    assert filter_terms(
        {
            "route": "rest",
            "query": "Observation?code=system|LAB&date=ge2025-01-01&_count=50&_include=Observation:subject",
        }
    ) == {"lab", "2025-01-01"}
    assert filter_terms(
        {
            "route": "sql",
            "query": "SELECT resource_ref FROM Resource WHERE json_extract(resource_json,'$.status')='active'",
        }
    ) == {"active"}


def test_unfiltered_type_read_counts_as_a_broad_opening_search():
    task, store = episode()
    store.request("GET", "Observation")
    metrics = search_behavior(task, store)
    assert metrics["search_calls"] == metrics["first_action_broad_search"] == 1
    assert metrics["first_action_targeted_search"] == 0


def test_id_hit_is_distinct_from_access_to_required_fields():
    task, store = episode()
    target = task["gold"]["evidence"][0]
    assert (
        query(
            store, f"SELECT resource_ref FROM Observation WHERE resource_ref='{target}'"
        )["status"]
        == 200
    )
    metrics = search_behavior(task, store)
    assert metrics["first_search_gold_resource_hit"] == 1
    assert metrics["first_search_required_fields_hit"] == 0
    assert metrics["first_action_targeted_search"] == 1
    assert metrics["first_turn_gold_resource_hit"] == 1
    assert metrics["first_turn_required_fields_hit"] == 0
    query(
        store,
        f"SELECT resource_ref, resource_json FROM Resource WHERE resource_ref='{target}'",
    )
    assert search_behavior(task, store)["repeated_queries"] == 0
    query(
        store,
        f"SELECT resource_ref, resource_json FROM Resource WHERE resource_ref='{target}'",
    )
    assert search_behavior(task, store)["repeated_queries"] == 1


def test_novelty_uses_prior_visible_responses_and_task_prompt():
    task, store = episode()
    pid = task["patient_id"]
    store.request("GET", "Patient/" + pid)
    store.request("GET", "Observation?patient=" + pid + "&code=made-up-code")
    metrics = search_behavior(task, store)
    assert metrics["novel_filter_terms"] == 1
    assert store.events[-1]["filter_terms_not_previously_observed"] == ["made-up-code"]
    store.events[-1].pop("filter_terms_not_previously_observed")
    assert search_behavior(task, store)["novel_filter_terms"] == -1


def test_first_turn_is_not_shifted_by_earlier_failed_actions():
    task, store = episode()
    store.round = 2  # first model round produced no executable tool call
    store.request("GET", "Observation?patient=" + task["patient_id"])
    metrics = search_behavior(task, store)
    assert metrics["first_action_targeted_search"] == 1
    assert metrics["first_turn_targeted_search"] == 0
    assert metrics["first_turn_gold_resource_hit"] == 0


def test_schema_first_is_not_mislabeled_as_document_first():
    task, store = episode()
    document = next(
        ref(r)
        for r in store.resources.values()
        if r["resourceType"] == "DocumentReference"
    )
    store.agent_tools = ["fhir_schema", "fhir_request"]
    store.request("GET", document)
    assert search_behavior(task, store)["first_action_document_read"] == 0


def test_timeline_hidden_values_do_not_enter_visible_vocabulary():
    task, store = episode()
    target = task["gold"]["evidence"][0]
    # A schema-valid private annotation returned only to the helper backend.
    store.resources[target]["note"] = [{"text": "unseen-word-739"}]
    store.active_tool = "chart_timeline"
    asyncio.run(chart_timeline(task["patient_id"], "Observation", _store=store))
    assert "unseen-word-739" not in store.observed_terms
    assert "valueQuantity" not in store.exposed[target]
    assert search_behavior(task, store)["first_search_required_fields_hit"] == 0
    store.active_tool = "fhir_request"
    store.request("GET", target)
    assert "unseen-word-739" in store.observed_terms


def test_discovery_variants_keep_original_writes_and_replay_all_branches():
    counts = set()
    snapshots = {}
    for split in ("train", "dev"):
        for task in load_jsonl(PILOT / split / "tasks.jsonl"):
            if task["family"] not in FAMILIES:
                continue
            snapshots.setdefault(task["shard"], load_jsonl(PILOT / task["shard"]))
            before = copy.deepcopy(task)
            variant = discovery_variant(task, snapshots[task["shard"]])
            assert task == before
            assert (
                variant["patient_id"] == task["patient_id"]
                and variant["split"] == task["split"]
            )
            assert variant["dependency_depth"] == 4
            assert task_dimensions(variant)["crud"] == task_dimensions(task)["crud"]
            for operation in ("updates", "creates", "deletes"):
                assert variant["gold"][operation] == task["gold"][operation]
            assert replay_task(PILOT, variant)["metrics"]["strict_success"] == 1
            for reference in variant["gold"]["evidence"]:
                assert reference not in variant["prompt"]
            # Structured prompt answer keys agree with the verifier contract.
            for field in variant["gold"]["answer"]:
                assert field in variant["prompt"]
            counts.add((variant["family"], task_dimensions(variant)["crud"]))
    assert {
        ("referral_closure_discovery", "R"),
        ("referral_closure_discovery", "RU"),
        ("referral_acknowledgement_discovery", "CRU"),
        ("claim_cleanup_discovery", "RD"),
    } <= counts


def test_loader_preserves_default_crud_and_exposes_optional_discovery_metadata():
    base = load_environment(pilot=True)
    expanded = load_environment(pilot=True, discovery_variants=True)
    assert set(base.task_index) < set(expanded.task_index)
    assert all(
        task == expanded.task_index[key] for key, task in base.task_index.items()
    )
    selected = load_environment(
        pilot=True, discovery_variants=True, family="claim_cleanup_discovery"
    )
    info = selected.eval_dataset[0]["info"]
    assert info["difficulty_bin"] == "very_hard" and info["retrieval_hops"] == 4
    assert info["crud"] == "RD" and info["required_mutation_entries"] == 1


def rollout(step=0, profile="raw", task_id="t", patient="p", success=1, **metrics):
    return {
        "training_step": step,
        "model": "test-fixture",
        "seed": 17,
        "tool_profile": profile,
        "query_mode": "rest",
        "benchmark_manifest_sha256": "a" * 64,
        "evaluation_config": {
            "environment_version": "0.3.1",
            "max_calls": 40,
            "max_turns": 20,
            "max_completion_tokens": 2048,
            "tool_protocol": "native",
            "answer_mode": "strict",
            "efficiency_weight": 0,
            "retrieval_weight": 0,
            "discovery_variants": True,
        },
        "info": {
            "task_id": task_id,
            "patient_id": patient,
            "family": "f",
            "split": "dev",
            "retrieval_hops": 2,
            "crud": "RU",
        },
        "metrics": {"strict_success": success, **metrics},
    }


def test_checkpoint_analysis_rejects_easier_or_unpaired_cohorts():
    with pytest.raises(ValueError, match="cohort"):
        normalize([rollout(), rollout(step=100, task_id="easier")])
    with pytest.raises(ValueError, match="identical"):
        normalize([rollout(), rollout(profile="assisted", task_id="different")])
    changed = rollout(step=100)
    changed["info"]["retrieval_hops"] = 1
    with pytest.raises(ValueError, match="dimensions"):
        normalize([rollout(), changed])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize([rollout(), rollout()])
    changed = rollout(step=100)
    changed["evaluation_config"]["max_turns"] = 10
    with pytest.raises(ValueError, match="budgets"):
        normalize([rollout(), changed])


def test_checkpoint_summary_separates_missing_usage_censoring_and_failed_cost():
    rows = [
        rollout(
            model_turns=4,
            token_usage_complete=1,
            model_output_tokens=100,
            all_evidence_round_observed=3,
            all_evidence_censored=0,
        ),
        rollout(
            task_id="u",
            patient="q",
            success=0,
            model_turns=1,
            model_output_tokens=0,
            all_evidence_round_observed=-1,
            all_evidence_censored=1,
        ),
    ]
    report = summarize(rows, resamples=20)
    group = next(g for g in report["groups"] if g["stratum"] == "difficulty")
    assert group["all_rollouts"]["strict_success"]["mean"] == 0.5
    assert group["all_rollouts"]["model_turns"]["mean"] == 2.5
    assert group["successful_rollouts_only"]["model_turns"]["mean"] == 4
    assert group["all_rollouts"]["model_output_tokens"]["missing"] == 1
    assert group["all_rollouts"]["all_evidence_round_observed"]["mean"] == 3
    assert group["all_rollouts"]["all_evidence_censored"]["mean"] == 0.5
    assert group["all_rollouts"]["strict_success"]["patient_ci95"] is not None


def test_offline_cli_exports_actual_input_rows_and_plots(tmp_path):
    pytest.importorskip("matplotlib")
    rows = [
        rollout(
            step=step,
            task_id=task,
            patient=patient,
            model_turns=3,
            token_usage_complete=1,
            model_output_tokens=25,
            all_evidence_round_observed=2,
        )
        for step in (0, 10)
        for task, patient in (("t", "p"), ("u", "q"))
    ]
    for row in rows:
        row["model"] = "TEST FIXTURE - not model results"
        row["fhir_trace"] = [
            {
                "route": "rest",
                "method": "GET",
                "round": 1,
                "query": "Patient?identifier=fixture",
                "status": 200,
                "refs": ["Patient/fixture"],
            }
        ]
    path = tmp_path / "rollouts.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    script = (
        Path(__file__).resolve().parents[1] / "scripts/analyze_query_experiments.py"
    )
    output = tmp_path / "report"
    env = dict(os.environ, MPLCONFIGDIR=str(tmp_path / "matplotlib"))
    result = subprocess.run(
        [sys.executable, str(script), str(path), "--output", str(output), "--plots"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((output / "summary.json").read_text())["rollouts"] == 4
    assert (output / "summary.csv").stat().st_size > 0
    examples = json.loads((output / "same-task-queries.json").read_text())
    assert len(examples) == 4 and all(row["trace_available"] for row in examples)
    assert {row["training_step"] for row in examples} == {0, 10}
    assert len(list(output.glob("*.png"))) == 2
