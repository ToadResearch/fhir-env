import asyncio
import copy
import json
from pathlib import Path

import pytest

from fhir_workflows.dataset import build_dataset, load_jsonl, MRN_SYSTEM
from fhir_workflows.cli import replay_task
from fhir_workflows.scoring import evaluate, state_success
from fhir_workflows.sql import query
from fhir_workflows.store import FhirStore
from fhir_workflows.validation import patient_ref, ref
from fhir_workflows.helpers import (
    find_patient,
    fhir_schema,
    read_document,
    chart_timeline,
    prepare_update,
)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    build_dataset(root)
    tasks = load_jsonl(root / "train/tasks.jsonl")
    return root, tasks


def episode(corpus, family):
    root, tasks = corpus
    t = next(t for t in tasks if t["family"] == family)
    resources = [r for r in load_jsonl(root / t["shard"]) if ref(r) not in t["omit"]]
    return t, FhirStore(
        resources, "Patient/" + t["patient_id"], t["writable_types"], t["delete_refs"]
    )


def final(t):
    return {"answer": t["gold"]["answer"], "evidence": t["gold"]["evidence"]}


def execute_steps(t, s, steps=None):
    for i, step in enumerate(steps or t["reference_steps"], 1):
        s.round = i
        assert (
            s.request(
                step["method"], step["path"], step.get("body"), step.get("headers")
            )["status"]
            < 300
        )


def test_all_reference_traces(corpus):
    root, tasks = corpus
    for task in tasks + load_jsonl(root / "dev/tasks.jsonl"):
        assert replay_task(root, task)["metrics"]["strict_success"] == 1


def test_deterministic_build_and_disjoint_patients(corpus, tmp_path):
    root, _ = corpus
    second = build_dataset(tmp_path)
    first = json.loads((root / "manifest.json").read_text())
    assert first == second
    train = {t["patient_id"] for t in load_jsonl(root / "train/tasks.jsonl")}
    dev = {t["patient_id"] for t in load_jsonl(root / "dev/tasks.jsonl")}
    assert not train & dev
    assert {True, False} <= {
        t["gold"]["answer"]["documented"]
        for t in load_jsonl(root / "train/tasks.jsonl")
        if t["family"] == "document_availability"
    }


def test_no_reward_for_unread_gold(corpus):
    t, s = episode(corpus, "latest_result")
    assert evaluate(t, s, final(t))["reward"] == 0


def test_unsupported_parameter_is_rejected(corpus):
    _, s = episode(corpus, "latest_result")
    assert s.request("GET", "Observation?unknown=anything")["status"] == 400
    assert s.request("GET", "Observation?code:contains=718-7")["status"] == 400


def test_repeated_and_tokens_and_paging(corpus):
    t, s = episode(corpus, "latest_result")
    assert (
        s.request(
            "GET",
            f"Observation?patient={t['patient_id']}&status=final&status=preliminary",
        )["body"]["total"]
        == 0
    )
    assert (
        s.request(
            "GET",
            f"Observation?patient={t['patient_id']}&code=http://wrong.example|718-7",
        )["body"]["total"]
        == 0
    )
    result = s.request("GET", "Observation?_count=1")
    assert len(result["body"]["entry"]) == 1
    next_path = result["body"]["link"][0]["url"]
    assert (
        s.request("GET", next_path)["body"]["entry"][0]["fullUrl"]
        != result["body"]["entry"][0]["fullUrl"]
    )


def test_include_and_chaining(corpus):
    t, s = episode(corpus, "linked_report")
    path = t["reference_steps"][2]["path"]
    result = s.request("GET", path)
    assert {e["resource"]["resourceType"] for e in result["body"]["entry"]} == {
        "DiagnosticReport",
        "Observation",
    }
    patient = s.initial["Patient/" + t["patient_id"]]
    chained = s.request(
        "GET",
        f"Coverage?patient.identifier={MRN_SYSTEM}|{patient['identifier'][0]['value']}&status=active",
    )
    assert chained["body"]["total"] == 1


def test_transaction_rolls_back_first_write(corpus):
    t, s = episode(corpus, "cancel_referral")
    transaction = copy.deepcopy(t["reference_steps"][-1]["body"])
    transaction["entry"][1]["request"]["ifMatch"] = 'W/"0"'
    assert s.request("POST", "", transaction)["status"] == 412
    assert s.resources == s.initial and s.audit == []
    assert s.events[-1]["primitive_operations"] == 2


def test_stale_update_preserves_concurrent_edit(corpus):
    t, s = episode(corpus, "cancel_referral")
    key = t["gold"]["updates"][0]["ref"]
    r = copy.deepcopy(s.resources[key])
    r["status"] = "revoked"
    s.resources[key]["meta"]["versionId"] = "2"
    s.resources[key]["note"] = [{"text": "Concurrent coordinator comment"}]
    assert s.request("PUT", key, r, {"If-Match": 'W/"1"'})["status"] == 412
    assert s.resources[key]["note"][0]["text"] == "Concurrent coordinator comment"


def test_wrong_patient_write_is_blocked(corpus):
    t, s = episode(corpus, "coverage_change")
    other = next(
        r
        for r in s.resources.values()
        if r["resourceType"] == "Coverage"
        and patient_ref(r) != "Patient/" + t["patient_id"]
    )
    changed = {**other, "status": "cancelled"}
    assert s.request("PUT", ref(other), changed, {"If-Match": 'W/"1"'})["status"] == 403
    assert s.unsafe_attempts == 1


def test_transient_unauthorized_field_edit_fails_scoring(corpus):
    t, s = episode(corpus, "coverage_change")
    key = t["gold"]["updates"][0]["ref"]
    changed = {**s.resources[key], "subscriberId": "corrupted"}
    assert s.request("PUT", key, changed, {"If-Match": 'W/"1"'})["status"] == 200
    assert (
        s.request("PUT", key, copy.deepcopy(s.initial[key]), {"If-Match": 'W/"2"'})[
            "status"
        ]
        == 200
    )
    transaction = copy.deepcopy(t["reference_steps"][-1]["body"])
    transaction["entry"][0]["request"]["ifMatch"] = 'W/"3"'
    assert s.request("POST", "", transaction)["status"] == 200
    assert s.resources[key]["status"] == "cancelled"
    assert s.resources[key]["subscriberId"] == s.initial[key]["subscriberId"]
    assert not state_success(t, s)


def test_delete_only_explicit_unreferenced_draft(corpus):
    t, s = episode(corpus, "delete_duplicate_draft")
    key = t["gold"]["deletes"][0]
    s.resources[key]["description"] = "still the selected erroneous draft"
    assert s.request("DELETE", key, headers={"If-Match": 'W/"1"'})["status"] == 204
    assert s.request("GET", key)["status"] == 410
    assert (
        s.request(
            "GET",
            "Task?identifier="
            + t["reference_steps"][0]["path"].split("identifier=")[1],
        )["body"]["total"]
        == 0
    )


def test_dangling_reference_write_rolls_back(corpus):
    _, s = episode(corpus, "message_followup")
    body = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": {"reference": s.writable_patient},
        "focus": {"reference": "DiagnosticReport/absent"},
    }
    assert s.request("POST", "Task", body)["status"] == 400
    assert s.resources == s.initial


def test_conditional_create_is_idempotent(corpus):
    t, s = episode(corpus, "message_followup")
    body = copy.deepcopy(t["reference_steps"][-1]["body"])
    body["identifier"] = [{"system": "https://example.test/id", "value": "unique"}]
    headers = {"If-None-Exist": "identifier=https://example.test/id|unique"}
    a = s.request("POST", "Task", body, headers)
    b = s.request("POST", "Task", body, headers)
    assert a["status"] == 201 and b["status"] == 200
    assert a["body"]["id"] == b["body"]["id"] and len(s.audit) == 1


def test_absence_requires_executed_search(corpus):
    root, tasks = corpus
    t = next(
        t
        for t in tasks
        if t.get("absence_scope") and t["family"] == "document_availability"
    )
    resources = [r for r in load_jsonl(root / t["shard"]) if ref(r) not in t["omit"]]
    s = FhirStore(resources)
    lookup = t["reference_steps"][0]
    s.request("GET", lookup["path"])
    assert evaluate(t, s, final(t))["reward"] == 0
    s.request("GET", t["reference_steps"][1]["path"])
    assert evaluate(t, s, final(t))["reward"] == 1
    assert evaluate(t, s, final(t))["all_evidence_call"] == 2


def test_bool_number_confusion_is_rejected(corpus):
    t, s = episode(corpus, "document_availability")
    execute_steps(t, s)
    bad = final(t)
    bad["answer"] = {"documented": int(bad["answer"]["documented"])}
    assert evaluate(t, s, bad)["reward"] == 0


def test_sql_rest_equivalence_and_field_exposure(corpus):
    t, s = episode(corpus, "latest_result")
    response = query(
        s,
        f"SELECT resource_ref, value, unit, date FROM Observation WHERE patient_ref='Patient/{t['patient_id']}' AND code='718-7' AND status='final' ORDER BY date DESC LIMIT 1",
    )
    assert (
        response["status"] == 200
        and response["rows"][0]["value"] == t["gold"]["answer"]["value"]
    )
    assert evaluate(t, s, final(t))["reward"] == 1
    _, only_ids = episode(corpus, "latest_result")
    query(
        only_ids,
        f"SELECT resource_ref FROM Observation WHERE patient_ref='Patient/{t['patient_id']}' AND code='718-7' ORDER BY date DESC LIMIT 1",
    )
    assert evaluate(t, only_ids, final(t))["reward"] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM Patient",
        "SELECT 1; DELETE FROM Patient",
        "SELECT * FROM Patient",
        "SELECT secret FROM oracle",
        "PRAGMA user_version",
        "WITH x AS (DELETE FROM Patient) SELECT 1",
        "SELECT randomblob(1000000000) FROM Patient LIMIT 1",
    ],
)
def test_sql_rejects_outside_subset(corpus, sql):
    _, s = episode(corpus, "latest_result")
    before = copy.deepcopy(s.resources)
    assert query(s, sql)["status"] == 400 and s.resources == before


def test_rollout_isolation(corpus):
    t, s = episode(corpus, "cancel_referral")
    _, other = episode(corpus, "cancel_referral")
    execute_steps(t, s)
    assert s.resources != other.resources and other.resources == other.initial


def test_read_responses_cannot_modify_shared_source(corpus):
    t, store = episode(corpus, "latest_result")
    _, other = episode(corpus, "latest_result")
    key = "Patient/" + t["patient_id"]
    response = store.request("GET", key)
    response["body"]["name"][0]["text"] = "corrupted"
    assert store.resources[key]["name"][0]["text"] != "corrupted"
    assert other.resources[key]["name"][0]["text"] != "corrupted"


def test_schema_rejects_non_fhir_fields(corpus):
    _, s = episode(corpus, "message_followup")
    body = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": {"reference": s.writable_patient},
        "nonFHIRfield": True,
    }
    assert s.request("POST", "Task", body)["status"] == 400


def test_dependency_depth_is_not_api_call_count(corpus):
    t, _ = episode(corpus, "linked_report")
    assert t["dependency_depth"] == 4 and t["reference_calls"] == 3
    follow, _ = episode(corpus, "message_followup")
    assert follow["dependency_depth"] == 4 and follow["workflow_depth"] == 5


def test_current_training_config_legacy_bridge():
    import tomllib
    from verifiers.v1.env import EnvConfig

    repo = Path(__file__).parents[1]
    for name in ["prime-rl.toml", "hosted-dedicated.template.toml"]:
        config = tomllib.loads((repo / "configs" / name).read_text())
        for role in ["train", "eval"]:
            for source in config["orchestrator"][role]["source"]:
                validated = EnvConfig.model_validate(source["env"])
                assert validated.is_legacy and validated.args["query_mode"] == "rest"


def test_verifiers_loader_and_tool_dispatch(corpus):
    from fhir_workflows import load_environment
    import verifiers as vf

    env = load_environment(data_dir=str(corpus[0]), query_mode="hybrid")
    assert all(
        "_store" not in tool.parameters.get("properties", {}) for tool in env.tool_defs
    )
    row = env.dataset[0]
    state = vf.State(
        {
            "info": row["info"],
            "prompt": row["prompt"],
            "answer": row["answer"],
            "task": "default",
        }
    )

    async def run():
        await env.setup_state(state)
        t = state["fhir_task"]
        step = t["reference_steps"][0]
        message = vf.AssistantMessage(
            tool_calls=[
                vf.ToolCall(
                    id="test",
                    name="fhir_request",
                    arguments=json.dumps(
                        {"method": step["method"], "path": step["path"]}
                    ),
                )
            ]
        )
        result = await env.env_response([message], state)
        assert json.loads(result[0].content)["status"] == 200
        assert state["fhir_store"].events[0]["round"] == 1
        state["completion"] = [vf.AssistantMessage(content=json.dumps(final(t)))]
        await env.rubric.score_rollout(state)
        assert state["reward"] == 1.0
        assert state["fhir_metrics"]["strict_success"] == 1.0

    asyncio.run(run())


def test_absence_search_cannot_hide_behind_extra_filters(corpus):
    root, tasks = corpus
    t = next(
        t
        for t in tasks
        if t.get("absence_scope") and t["family"] == "document_availability"
    )
    resources = [r for r in load_jsonl(root / t["shard"]) if ref(r) not in t["omit"]]
    s = FhirStore(resources)
    s.request("GET", t["reference_steps"][0]["path"])
    s.request("GET", t["reference_steps"][1]["path"] + "&status=entered-in-error")
    assert evaluate(t, s, final(t))["reward"] == 0


def test_extra_clinical_fields_on_create_fail(corpus):
    t, s = episode(corpus, "message_followup")
    execute_steps(t, s, t["reference_steps"][:-1])
    body = copy.deepcopy(t["reference_steps"][-1]["body"])
    body["statusReason"] = {"text": "Invented clinical conclusion"}
    assert s.request("POST", "Task", body)["status"] == 201
    assert evaluate(t, s, final(t))["reward"] == 0


def test_helper_identity_does_not_choose_ambiguous_patient(corpus):
    t, s = episode(corpus, "ambiguous_identity")
    name = s.resources["Patient/" + t["patient_id"]]["name"][0]["text"]
    result = json.loads(asyncio.run(find_patient(name=name, _store=s)))
    assert result["identity_resolution"] == "ambiguous"
    assert result["body"]["total"] == 2
    assert s.audit == [] and len(s.events) == 1


def test_helper_prepare_update_requires_separate_commit(corpus):
    t, s = episode(corpus, "correct_erroneous_result")
    key = t["gold"]["updates"][0]["ref"]
    prepared = json.loads(
        asyncio.run(prepare_update(key, '{"status":"entered-in-error"}', _store=s))
    )
    assert prepared["committed"] is False and s.audit == []
    assert s.resources[key]["status"] == "final"
    assert (
        s.request(
            prepared["method"], prepared["path"], prepared["body"], prepared["headers"]
        )["status"]
        == 200
    )
    assert evaluate(t, s, final(t))["strict_success"] == 1
    with pytest.raises(ValueError):
        asyncio.run(prepare_update(key, '{"id":"other"}', _store=s))


def test_timeline_index_is_not_lab_value_evidence(corpus):
    t, s = episode(corpus, "latest_result")
    result = json.loads(
        asyncio.run(
            chart_timeline(t["patient_id"], resource_types="Observation", _store=s)
        )
    )
    assert any(e["reference"] in t["gold"]["evidence"] for e in result["entries"])
    assert evaluate(t, s, final(t))["reward"] == 0
    assert evaluate(t, s, final(t))["first_evidence_call"] is None
    assert len(s.events) == 1 and len(s.assistance) == 1


def test_document_helper_decodes_visible_narrative_and_schema_is_static(corpus):
    t, s = episode(corpus, "longitudinal_lab_trend")
    document = next(
        r
        for r in s.resources.values()
        if r["resourceType"] == "DocumentReference"
        and patient_ref(r) == "Patient/" + t["patient_id"]
        and r.get("type", {}).get("coding", [{}])[0].get("code")
        == "synthetic-followup-profile"
    )
    result = json.loads(
        asyncio.run(read_document(ref(document), max_characters=40, _store=s))
    )
    assert (
        result["text"].startswith("GENERATED FOLLOW-UP") and result["next_start"] == 40
    )
    before = s.seen.copy()
    shape = json.loads(asyncio.run(fhir_schema("Task", _store=s)))
    assert "status" in shape["fields"] and s.seen == before and s.audit == []


def test_assistance_ablation_preserves_dataset_and_gold(corpus):
    from fhir_workflows.fhir_workflows import load_environment

    root, _ = corpus
    raw = load_environment(data_dir=str(root), tool_profile="raw", max_examples=12)
    assisted = load_environment(
        data_dir=str(root), tool_profile="assisted", max_examples=12
    )
    assert (
        raw.dataset.select_columns(["question", "answer", "info"]).to_list()
        == assisted.dataset.select_columns(["question", "answer", "info"]).to_list()
    )
    assert raw.task_index == assisted.task_index
    assert len(assisted.tools) == len(raw.tools) + 5


def test_longitudinal_narratives_are_linked_and_do_not_modify_source_list(corpus):
    t, s = episode(corpus, "medication_reconciliation")
    before = {
        key: copy.deepcopy(r)
        for key, r in s.resources.items()
        if r["resourceType"] == "MedicationStatement"
    }
    execute_steps(t, s)
    assert evaluate(t, s, final(t))["strict_success"] == 1
    assert all(s.resources[key] == value for key, value in before.items())
    assert any(r["resourceType"] == "Composition" for r in s.resources.values())


def test_literal_no_tools_control_is_read_only_and_supplies_visible_context(corpus):
    import verifiers as vf
    from fhir_workflows.fhir_workflows import load_environment

    root, _ = corpus
    env = load_environment(
        data_dir=str(root), tool_profile="none", family="latest_result", max_examples=2
    )
    assert not env.tools
    row = env.dataset[0]
    assert "pre-supplied no-tools control" in row["question"]
    state = vf.State(
        {
            "info": row["info"],
            "prompt": row["prompt"],
            "answer": row["answer"],
            "task": "default",
        }
    )
    asyncio.run(env.setup_state(state))
    metrics = evaluate(
        state["fhir_task"], state["fhir_store"], final(state["fhir_task"])
    )
    assert metrics["strict_success"] == 1 and metrics["tool_calls"] == 0
    assert metrics["context_bytes"] > 0 and metrics["first_evidence_call"] == 0
    with pytest.raises(ValueError):
        load_environment(
            data_dir=str(root), tool_profile="none", family="coverage_change"
        )


def test_pediatric_reporter_and_correlated_weight_history():
    from fhir_workflows.dataset import demo_source, make_world

    source = copy.deepcopy(demo_source()[0])
    source["age"] = 3
    resources, _, handles, _ = make_world(source, "Jordan Synthetic", 17)
    assert handles["medication-discrepancy"]["sender"]["reference"] == ref(
        handles["caregiver"]
    )
    values = [
        r["valueQuantity"]["value"]
        for r in resources
        if r["resourceType"] == "Observation"
        and r.get("code", {}).get("coding", [{}])[0].get("code") == "measured-weight"
    ]
    assert len(values) == 3 and max(values) - min(values) <= 0.5
    assert max(values) < 25


def test_timeline_window_does_not_expose_filtered_resources(corpus):
    t, s = episode(corpus, "latest_result")
    result = json.loads(
        asyncio.run(
            chart_timeline(
                t["patient_id"],
                resource_types="Observation",
                start_date="2099-01-01",
                _store=s,
            )
        )
    )
    assert result["entries"] == [] and not s.seen
    assert s.events[-1]["refs"] == [] and s.events[-1]["backend_refs"]
    assert s.events[-1]["bytes"] > 0
