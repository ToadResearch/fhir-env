"""Scenario consistency, CRUD safety and retrieval ablations for the v3 compiler."""

import copy
import json

import pytest

from fhir_workflows.cli import replay_task
from fhir_workflows.dataset import (
    build_dataset,
    compile_tasks,
    demo_source,
    load_jsonl,
    make_world,
)
from fhir_workflows.fhir_workflows import load_environment
from fhir_workflows.scoring import evaluate
from fhir_workflows.sql import query
from fhir_workflows.store import FhirStore
from fhir_workflows.validation import patient_ref, ref, validate_graph


@pytest.fixture(scope="module")
def expanded(tmp_path_factory):
    root = tmp_path_factory.mktemp("expanded")
    manifest = build_dataset(root)
    tasks = load_jsonl(root / "train/tasks.jsonl") + load_jsonl(
        root / "dev/tasks.jsonl"
    )
    return root, manifest, tasks


def case(expanded, family):
    root, _, tasks = expanded
    t = next(t for t in tasks if t["family"] == family)
    resources = [r for r in load_jsonl(root / t["shard"]) if ref(r) not in t["omit"]]
    return t, FhirStore(
        resources,
        "Patient/" + t["patient_id"],
        t["writable_types"],
        t["delete_refs"],
        writable_refs=t.get("writable_refs", ()),
    )


def execute(store, steps):
    for i, step in enumerate(steps, 1):
        store.round = i
        result = store.request(
            step["method"], step["path"], step.get("body"), step.get("headers")
        )
        assert result["status"] < 300, result


def final(task):
    return {"answer": task["gold"]["answer"], "evidence": task["gold"]["evidence"]}


def test_expanded_family_and_resource_coverage(expanded):
    _, m, tasks = expanded
    assert m["distinct_families"] >= 34
    assert len(m["resources"]) >= 49
    assert {
        "registration",
        "nursing",
        "diagnostics",
        "scheduling",
        "referral",
        "medication",
        "immunization",
        "revenue",
        "equipment",
        "records",
    } <= set(m["domains"])
    assert {"CRU", "CR", "RU", "RD", "R"} <= set(m["crud"])
    assert len({t["id"] for t in tasks}) == len(tasks)
    root = expanded[0]
    for t in tasks:
        assert replay_task(root, t)["metrics"]["strict_success"] == 1


def test_slot_changes_are_atomic_and_auxiliary_scope_is_bounded(expanded):
    t, store = case(expanded, "reschedule_with_slot_release")
    execute(store, t["reference_steps"][:-1])
    bundle = t["reference_steps"][-1]["body"]
    first = bundle["entry"][0]
    before = copy.deepcopy(store.resources)
    bad = store.request(
        "PUT",
        first["request"]["url"],
        first["resource"],
        {"If-Match": first["request"]["ifMatch"]},
    )
    assert (
        bad["status"] == 400
    )  # booking points at a free slot; whole write rolled back
    assert store.resources == before and store.audit == []
    execute(store, [t["reference_steps"][-1]])
    assert evaluate(t, store, final(t))["strict_success"] == 1
    other = next(
        r
        for k, r in store.resources.items()
        if r["resourceType"] == "Slot" and k not in t["writable_refs"]
    )
    assert (
        store.request(
            "PUT", ref(other), {**other, "status": "free"}, {"If-Match": 'W/"1"'}
        )["status"]
        == 403
    )


def test_writing_before_reading_cannot_be_repaired_by_later_reads(expanded):
    t, store = case(expanded, "verified_contact_change")
    steps = t["reference_steps"]
    execute(store, [steps[-1], *steps[:-1]])
    assert evaluate(t, store, final(t))["answer_success"] == 1
    assert evaluate(t, store, final(t))["strict_success"] == 0


def test_conditional_refill_retry_is_idempotent(expanded):
    t, store = case(expanded, "refill_request_routing")
    execute(store, t["reference_steps"])
    execute(store, [t["reference_steps"][-1]])
    assert len(store.audit) == 1
    assert evaluate(t, store, final(t))["strict_success"] == 1
    retry = {**t["reference_steps"][-1], "headers": {}}
    execute(store, [retry])
    assert evaluate(t, store, final(t))["strict_success"] == 0


def test_appointment_and_account_are_patient_scoped(expanded):
    t, store = case(expanded, "cancel_visit_release_capacity")
    appt = next(
        r
        for r in store.resources.values()
        if r["resourceType"] == "Appointment"
        and r.get("slot")
        and patient_ref(r) == "Patient/" + t["patient_id"]
    )
    assert patient_ref(appt) == "Patient/" + t["patient_id"]
    other = next(
        r
        for r in store.resources.values()
        if r["resourceType"] == "Patient" and r["id"] != t["patient_id"]
    )
    corrupted = copy.deepcopy(appt)
    corrupted["participant"][0]["actor"] = {"reference": ref(other)}
    assert (
        store.request("PUT", ref(appt), corrupted, {"If-Match": 'W/"1"'})["status"]
        == 403
    )
    assert (
        patient_ref(
            {"resourceType": "Account", "subject": [{"reference": "Patient/a"}]}
        )
        == "Patient/a"
    )
    with pytest.raises(ValueError, match="Multi-patient"):
        patient_ref(
            {
                "resourceType": "Account",
                "subject": [{"reference": "Patient/a"}, {"reference": "Patient/b"}],
            }
        )


def test_new_types_available_via_lossless_sql_without_id_only_reward(expanded):
    t, store = case(expanded, "date_specific_eligibility")
    refs = ",".join("'" + k + "'" for k in t["gold"]["evidence"])
    assert (
        query(
            store, f"SELECT resource_ref FROM Resource WHERE resource_ref IN ({refs})"
        )["status"]
        == 200
    )
    assert evaluate(t, store, final(t))["strict_success"] == 0
    out = query(
        store,
        f"SELECT resource_ref, resource_json FROM Resource WHERE resource_ref IN ({refs})",
    )
    assert out["status"] == 200
    assert all(json.loads(row["resource_json"])["id"] for row in out["rows"])
    assert evaluate(t, store, final(t))["strict_success"] == 1
    assert (
        query(
            store,
            "SELECT resource_ref FROM Resource WHERE json_extract(resource_json, '$.status')='active'",
        )["status"]
        == 200
    )
    assert query(store, "SELECT load_extension('oops') FROM Resource")["status"] == 400


def test_sql_can_supply_prewrite_evidence(expanded):
    t, store = case(expanded, "verified_contact_change")
    refs = ",".join("'" + k + "'" for k in t["gold"]["evidence"])
    query(
        store,
        f"SELECT resource_ref, resource_json FROM Resource WHERE resource_ref IN ({refs})",
    )
    execute(store, [t["reference_steps"][-1]])
    assert evaluate(t, store, final(t))["strict_success"] == 1


def test_pediatric_modules_weights_and_uncertainty():
    for age in (0, 3, 10, 16, 35, 80):
        s = {**demo_source()[0], "source_id": "age-check-" + str(age), "age": age}
        world, _, h, facts = make_world(s, "Test Person", 17)
        validate_graph(world)
        modules = facts["workflows"]["modules"]
        if age < 5:
            assert "medication" not in modules and "referral" not in modules
        if age < 1:
            assert "immunization" not in modules
        tasks = compile_tasks(s, h, facts)
        for t in tasks:
            if t["family"] == "nursing_observation_and_visit_close":
                assert (
                    abs(
                        t["gold"]["answer"]["weight_kg"]
                        - h["followup-weight-2"]["valueQuantity"]["value"]
                    )
                    <= 0.21
                )
            if t["family"] == "reported_allergy_with_uncertainty":
                assert (
                    t["gold"]["creates"][0]["verificationStatus"]["coding"][0]["code"]
                    == "unconfirmed"
                )
            if (
                t["family"] == "outside_immunization_documentation"
                and not t["gold"]["answer"]["exact_date_known"]
            ):
                assert "occurrenceDateTime" not in t["gold"]["creates"][0]
                assert t["gold"]["creates"][0]["primarySource"] is False


def test_no_inflated_hops_for_supplied_identifiers(expanded):
    for t in expanded[2]:
        if t.get("domain") and t["family"] != "imaging_report_linkage":
            assert t["dependency_depth"] == 1
    t, _ = case(expanded, "imaging_report_linkage")
    assert t["dependency_depth"] == 3
    assert t["information_regime"] == "order_identifier_only"
    for k in t["gold"]["evidence"]:
        if k.startswith(("DiagnosticReport/", "ImagingStudy/")):
            assert k not in t["prompt"]


def test_semantic_constraints_catch_schema_valid_corruption(expanded):
    _, store = case(expanded, "outside_immunization_documentation")
    resources = copy.deepcopy(list(store.resources.values()))
    imm = next(r for r in resources if r["resourceType"] == "Immunization")
    imm["occurrenceString"] = "sometime last year"
    with pytest.raises(ValueError, match="exactly one occurrence"):
        validate_graph(resources)
    resources = copy.deepcopy(list(store.resources.values()))
    account = next(r for r in resources if r["resourceType"] == "Account")
    patient = next(
        r
        for r in resources
        if r["resourceType"] == "Patient" and ref(r) != patient_ref(account)
    )
    account["coverage"][0]["coverage"] = {
        "reference": next(
            ref(r)
            for r in resources
            if r["resourceType"] == "Coverage" and patient_ref(r) == ref(patient)
        )
    }
    with pytest.raises(ValueError, match="Cross-patient"):
        validate_graph(resources)


def test_loader_domain_role_crud_filters(expanded):
    root = str(expanded[0])
    env = load_environment(
        data_dir=root, domains=["scheduling"], crud="U", max_examples=2
    )
    assert len(env.dataset) == 2
    assert all(t.get("domain") == "scheduling" for t in env.task_index.values())
    with pytest.raises(ValueError, match="crud must"):
        load_environment(data_dir=root, crud="X")
