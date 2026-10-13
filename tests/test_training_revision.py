"""Training feedback must not weaken evidence, identity or mutation checks."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
import verifiers.v1 as vf
from fhir_query_rl.dataset import load_jsonl
from fhir_query_rl.fhir_query_rl import load_environment
from fhir_query_rl.protocol import parse_object
from fhir_query_rl.scoring import evaluate_training
from fhir_query_rl.sql import query
from fhir_query_rl.store import FhirStore
from fhir_query_rl.validation import ref

PILOT = (
    Path(__file__).resolve().parents[1]
    / "environments/fhir_query_rl/fhir_query_rl/pilot"
)


def episode(family="latest_result"):
    task = next(
        t for t in load_jsonl(PILOT / "train/tasks.jsonl") if t["family"] == family
    )
    resources = [
        r for r in load_jsonl(PILOT / task["shard"]) if ref(r) not in task["omit"]
    ]
    return task, FhirStore(
        resources,
        "Patient/" + task["patient_id"],
        task["writable_types"],
        task["delete_refs"],
    )


def answer(task):
    return json.dumps(
        {"answer": task["gold"]["answer"], "evidence": task["gold"]["evidence"]}
    )


def execute(task, store, steps=None):
    for i, step in enumerate(steps or task["reference_steps"], 1):
        store.round = i
        assert (
            store.request(
                step["method"], step["path"], step.get("body"), step.get("headers")
            )["status"]
            < 300
        )


def test_fenced_answer_has_separate_semantic_and_strict_scores():
    task, store = episode()
    execute(task, store)
    fenced = "```json\n" + answer(task) + "\n```"
    strict = evaluate_training(task, store, fenced)
    canonical = evaluate_training(task, store, fenced, answer_mode="json_or_fence")
    assert strict["strict_success"] == canonical["strict_success"] == 0
    assert strict["reward"] == 0 and canonical["reward"] == 1
    assert strict["canonical_success"] == canonical["workflow_success"] == 1
    assert canonical["first_evidence_call_observed"] == 1
    assert canonical["query_predicate_count_max"] >= 4
    assert (
        evaluate_training(task, store, "Done.\n" + fenced, answer_mode="json_or_fence")[
            "reward"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "text",
    [
        '{"tool":"a","tool":"b","args":{}}',
        '{"value":NaN}',
        '[{"tool":"x"}]',
        '<|python_tag|>{"tool":"x","args":{}}',
        '{"tool":"x"} {"tool":"y"}',
    ],
)
def test_action_json_is_never_repaired(text):
    with pytest.raises(ValueError):
        parse_object(text)


def test_no_shaping_for_unread_answers_wrong_patients_or_id_only_sql():
    task, store = episode()
    assert (
        evaluate_training(task, store, answer(task), retrieval_weight=0.2)["reward"]
        == 0
    )
    other = next(
        r
        for r in store.resources.values()
        if r["resourceType"] == "Patient" and r["id"] != task["patient_id"]
    )
    store.request("GET", ref(other))
    assert evaluate_training(task, store, "{}", retrieval_weight=0.2)["reward"] == 0
    target = task["gold"]["evidence"][0]
    query(
        store,
        f"SELECT resource_ref FROM Observation WHERE resource_ref='{target}' LIMIT 1",
    )
    assert evaluate_training(task, store, "{}", retrieval_weight=0.2)["reward"] == 0


def test_shaping_rewards_field_exposure_without_claiming_completed_workflow():
    task, store = episode("correct_erroneous_result")
    execute(task, store, task["reference_steps"][:1])
    metrics = evaluate_training(task, store, "{}", retrieval_weight=0.2)
    assert metrics["reward"] == 0.2 and metrics["retrieval_progress"] == 1
    assert (
        metrics["strict_success"]
        == metrics["workflow_success"]
        == metrics["state_success"]
        == 0
    )


def test_restore_after_corrupt_write_cannot_receive_shaping():
    task, store = episode("correct_erroneous_result")
    execute(task, store, task["reference_steps"][:1])
    key = task["gold"]["evidence"][0]
    resource = copy.deepcopy(store.resources[key])
    resource["valueQuantity"]["value"] += 1
    assert store.request("PUT", key, resource, {"If-Match": 'W/"1"'})["status"] < 300
    resource["valueQuantity"]["value"] -= 1
    resource["status"] = "entered-in-error"
    assert store.request("PUT", key, resource, {"If-Match": 'W/"2"'})["status"] < 300
    metrics = evaluate_training(task, store, answer(task), retrieval_weight=0.2)
    assert metrics["reward"] == metrics["mutation_integrity"] == 0


def test_removed_legacy_protocol_is_rejected():
    with pytest.raises(TypeError, match="Unknown native v1"):
        load_environment(pilot=True, tool_protocol="json_action")


def test_supplied_context_gets_no_retrieval_bonus():
    env = load_environment(
        pilot=True, tool_profile="none", family="latest_result", max_examples=1
    )
    from fhir_query_rl.fhir_query_rl import FhirState

    task = next(iter(env.taskset))
    trace = vf.Trace(
        task=vf.TraceTask(type="FhirTask", data=task.data),
        agent=vf.AgentInfo(config=env.config.agent),
        state=FhirState(),
    )
    asyncio.run(task.setup(trace, None))
    metrics = evaluate_training(
        task.data.case,
        FhirStore.from_snapshot(trace.state.snapshot),
        "{}",
        retrieval_weight=0.2,
    )
    assert metrics["retrieval_progress"] == 1 and metrics["retrieval_shaping"] == 0
