"""Do not award evidence for synthetic columns masquerading as chart fields."""

import pytest

from fhir_query_rl.dataset import compile_tasks, demo_source, make_world
from fhir_query_rl.scoring import evaluate
from fhir_query_rl.sql import query
from fhir_query_rl.store import FhirStore


@pytest.fixture
def episode():
    source = demo_source()[0]
    resources, _, handles, facts = make_world(source, "Test Person", 17)
    task = next(
        t
        for t in compile_tasks(source, handles, facts)
        if t["family"] == "verified_contact_change"
    )
    return task, FhirStore(
        resources,
        "Patient/" + task["patient_id"],
        task["writable_types"],
        task["delete_refs"],
        writable_refs=task["writable_refs"],
    )


def test_null_field_forgery_cannot_satisfy_prewrite_evidence(episode):
    task, store = episode
    fields = sorted(set().union(*map(set, task["gold"]["evidence_fields"].values())))
    selected = ", ".join('"' + field + '"' for field in fields)
    constants = ", ".join('NULL AS "' + field + '"' for field in fields)
    refs = ",".join("'" + key + "'" for key in task["gold"]["evidence"])
    forged = query(
        store,
        f"SELECT resource_ref, {selected} FROM "
        f"(SELECT resource_ref, {constants} FROM Resource "
        f"WHERE resource_ref IN ({refs})) AS fake",
    )
    assert forged["status"] == 400
    assert not store.seen and not store.exposed
    write = task["reference_steps"][-1]
    assert (
        store.request(write["method"], write["path"], write["body"], write["headers"])[
            "status"
        ]
        == 200
    )
    metrics = evaluate(
        task,
        store,
        {"answer": task["gold"]["answer"], "evidence": task["gold"]["evidence"]},
    )
    assert metrics["strict_success"] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "WITH fake AS (SELECT resource_ref, NULL AS status FROM Resource) "
        "SELECT resource_ref, status FROM fake",
        "SELECT r.resource_ref, (SELECT MAX(status) FROM Observation) AS status "
        "FROM Resource AS r",
        "SELECT resource_ref FROM Resource WHERE resource_ref IN "
        "(SELECT resource_ref FROM Patient)",
    ],
)
def test_nested_queries_are_rejected_without_evidence_credit(episode, sql):
    _, store = episode
    assert query(store, sql)["status"] == 400
    assert not store.seen and not store.exposed


def test_direct_base_table_join_retains_evidence_lineage(episode):
    task, store = episode
    output = query(
        store,
        "SELECT r.resource_ref, r.resource_json, p.resource_ref AS patient_ref, "
        "p.identifier, p.name, p.birth_date "
        "FROM Resource AS r JOIN Patient AS p ON r.patient_ref=p.resource_ref "
        f"WHERE p.resource_ref='Patient/{task['patient_id']}' LIMIT 1",
    )
    assert output["status"] == 200
    assert output["rows"]
    for row in output["rows"]:
        assert (
            set(store.resources[row["resource_ref"]])
            <= store.exposed[row["resource_ref"]]
        )
        assert {"identifier", "name", "birthDate"} <= store.exposed[row["patient_ref"]]


def test_quoted_unknown_columns_cannot_forge_field_exposure(episode):
    task, store = episode
    fields = sorted(set().union(*map(set, task["gold"]["evidence_fields"].values())))
    selected = ", ".join('"' + field + '"' for field in fields)
    refs = ",".join("'" + key + "'" for key in task["gold"]["evidence"])
    output = query(
        store,
        f"SELECT resource_ref, {selected} FROM Resource WHERE resource_ref IN ({refs})",
    )
    # SQLite builds may reject unknown quoted identifiers or return string
    # constants for them; neither behavior should grant chart-field exposure.
    assert output["status"] in {200, 400}
    assert all(fields <= {"resource_ref"} for fields in store.exposed.values())


@pytest.fixture
def latest_result_episode():
    source = demo_source()[0]
    resources, _, handles, facts = make_world(source, "Test Person", 17)
    task = next(
        t
        for t in compile_tasks(source, handles, facts)
        if t["family"] == "latest_result"
    )
    return task, FhirStore(resources, "Patient/" + task["patient_id"])


@pytest.mark.parametrize("projection", ["unit, date", "value, date"])
def test_partial_measurement_projection_does_not_expose_quantity(
    latest_result_episode, projection
):
    task, store = latest_result_episode
    target = task["gold"]["evidence"][0]
    output = query(
        store,
        f"SELECT resource_ref, {projection} FROM Observation WHERE resource_ref='{target}'",
    )
    assert output["status"] == 200 and output["rows"]
    assert "valueQuantity" not in store.exposed[target]
    assert (
        evaluate(task, store, {"answer": task["gold"]["answer"], "evidence": [target]})[
            "strict_success"
        ]
        == 0
    )


def test_measurement_columns_from_different_aliases_do_not_combine(
    latest_result_episode,
):
    task, store = latest_result_episode
    target = task["gold"]["evidence"][0]
    output = query(
        store,
        "SELECT a.resource_ref, a.unit, a.date, b.value FROM Observation a "
        "JOIN Observation b ON a.resource_ref=b.resource_ref "
        f"WHERE a.resource_ref='{target}'",
    )
    assert output["status"] == 200 and output["rows"]
    assert "valueQuantity" not in store.exposed[target]


def test_complete_measurement_projection_keeps_success(latest_result_episode):
    task, store = latest_result_episode
    target = task["gold"]["evidence"][0]
    output = query(
        store,
        f"SELECT resource_ref, value, unit, date FROM Observation WHERE resource_ref='{target}'",
    )
    assert output["status"] == 200 and output["rows"]
    assert "valueQuantity" in store.exposed[target]
    assert (
        evaluate(task, store, {"answer": task["gold"]["answer"], "evidence": [target]})[
            "strict_success"
        ]
        == 1
    )
