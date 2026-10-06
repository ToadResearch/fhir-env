"""Regression gates for the extended corpus and standards-based serialization."""

import copy
from collections import Counter

import pytest

from fhir_query_rl.dataset import build_dataset, load_jsonl, MRN_SYSTEM
from fhir_query_rl.fhir_query_rl import load_environment
from fhir_query_rl.generation import (
    EpisodeCompiler,
    enhanced_plan,
    enhance_dataset,
    digest,
)
from fhir_query_rl.resource_contracts import validate_contracts
from fhir_query_rl.scoring import absence_checked
from fhir_query_rl.store import FhirStore
from fhir_query_rl.validation import ref
from fhir_query_rl.validation import validate_graph


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    base = tmp_path_factory.mktemp("base")
    bm = build_dataset(base)
    new = tmp_path_factory.mktemp("extended-parent") / "snapshot"
    nm = enhance_dataset(base, new)
    return base, new, bm, nm


def test_no_coverage_loss_and_every_reference_plan_passes(pipeline):
    base, new, bm, nm = pipeline
    assert nm["generation"]["reference_tasks_replayed"] == sum(nm["tasks"].values())
    assert set(bm["families"]) <= set(nm["families"])
    assert set(bm["resources"]) < set(nm["resources"])
    assert nm["distinct_families"] == bm["distinct_families"] + 10
    assert nm["patients"] == bm["patients"]
    assert (
        nm["structure"]["enhanced"]["distinct_shapes"]
        > nm["structure"]["baseline"]["distinct_shapes"]
    )
    for path, checksum in bm["files"].items():
        assert digest(base / path) == checksum
    for split in bm["tasks"]:
        old = {t["id"] for t in load_jsonl(base / split / "tasks.jsonl")}
        tasks = load_jsonl(new / split / "tasks.jsonl")
        assert old <= {t["id"] for t in tasks}
        assert len({t["id"] for t in tasks}) == len(tasks)


def test_source_attachments_preserved_exactly(pipeline):
    base, new, bm, _ = pipeline
    for shard in (p for p in bm["files"] if p.endswith(".ndjson")):
        old = {ref(r): r for r in load_jsonl(base / shard)}
        current = {ref(r): r for r in load_jsonl(new / shard)}
        for key, r in old.items():
            if any(
                t.get("code") == "source-preserved"
                for t in r.get("meta", {}).get("tag", [])
            ):
                assert current[key] == r


def test_snapshot_immutable_and_repeatable(pipeline, tmp_path):
    base, new, _, manifest = pipeline
    again = enhance_dataset(base, tmp_path / "again")
    assert again == manifest
    with pytest.raises(ValueError, match="immutable"):
        enhance_dataset(base, new)


@pytest.mark.parametrize("i", range(12))
def test_patient_local_timeline_and_serialization_variants(i):
    age = (5, 30, 50, 70)[i % 4]
    patient = {
        "resourceType": "Patient",
        "id": f"p{i}",
        "birthDate": f"{2025-age}-01-01",
        "identifier": [{"system": MRN_SYSTEM, "value": f"P-{i}"}],
    }
    plan = enhanced_plan(patient, "2025-10-06", i, 17, "dev/shards/0000.ndjson")
    compiled = EpisodeCompiler(patient, plan).compile()
    assert compiled == EpisodeCompiler(patient, plan).compile()
    validate_contracts(compiled["resources"])
    index = {ref(r): r for r in compiled["resources"]}
    appt = index[compiled["labels"]["appointment"]]
    assert appt["start"][:10] > plan["snapshot_date"]
    assert (
        index[compiled["labels"]["eligibility-request"]]["servicedDate"]
        == appt["start"][:10]
    )
    assert (
        index[compiled["labels"]["lab-order"]]["authoredOn"]
        < index[compiled["labels"]["lab-report"]]["issued"]
    )
    for r in compiled["history"]:
        assert int(r["meta"]["versionId"]) < int(index[ref(r)]["meta"]["versionId"])


def test_source_absence_scoped_by_origin_not_other_medications(pipeline):
    _, root, _, _ = pipeline
    task = next(
        t
        for t in load_jsonl(root / "train/tasks.jsonl")
        if t["family"] == "documented_home_medications" and t.get("absence_scope")
    )
    resources = load_jsonl(root / task["shard"])
    store = FhirStore(resources)
    query = f"MedicationStatement?patient={task['patient_id']}&_count=50"
    assert store.request("GET", query)["body"]["total"] > 0
    assert not absence_checked(task, store)
    assert (
        store.request("GET", query + "&_tag=" + task["absence_scope"]["tag"])["body"][
            "total"
        ]
        == 0
    )
    assert absence_checked(task, store)


def test_nested_reference_and_scalar_identifier_contracts():
    r = {
        "resourceType": "AppointmentResponse",
        "id": "r",
        "appointment": {"reference": "Appointment/a"},
        "actor": {"reference": "Patient/p"},
        "participantStatus": "accepted",
    }
    validate_contracts([r])
    bad = copy.deepcopy(r)
    bad["appointment"] = {"reference": "Task/t"}
    with pytest.raises(ValueError, match="invalid reference"):
        validate_contracts([bad])
    response = {
        "resourceType": "QuestionnaireResponse",
        "id": "q",
        "status": "completed",
        "identifier": [{"value": "a"}, {"value": "b"}],
    }
    with pytest.raises(ValueError, match="cardinality"):
        validate_contracts([response])


def test_runtime_rejects_contract_invalid_write(pipeline):
    _, root, _, _ = pipeline
    t = next(
        t
        for t in load_jsonl(root / "train/tasks.jsonl")
        if t["family"] == "patient_declines_appointment"
    )
    resources = load_jsonl(root / t["shard"])
    store = FhirStore(
        resources,
        "Patient/" + t["patient_id"],
        t["writable_types"],
        writable_refs=t["writable_refs"],
    )
    r = next(
        r
        for r in resources
        if r["resourceType"] == "AppointmentResponse"
        and r["actor"]["reference"] == store.writable_patient
    )
    bad = copy.deepcopy(r)
    bad["appointment"] = {
        "reference": next(ref(x) for x in resources if x["resourceType"] == "Task")
    }
    result = store.request(
        "PUT", ref(r), bad, {"If-Match": f'W/"{r["meta"]["versionId"]}"'}
    )
    assert result["status"] == 400 and store.resources[ref(r)] == r


def test_panel_includes_and_scalar_identifier_search(pipeline):
    _, root, _, _ = pipeline
    plans = load_jsonl(root / "generation/episodes.jsonl")
    case = next(
        p
        for p in plans
        if p["plan"]["panel_encoding"] == "members"
        and p["plan"]["lab_branch"] in {"final", "corrected"}
    )
    resources = load_jsonl(root / case["shard"])
    store = FhirStore(resources)
    panel = next(r for r in resources if ref(r) == case["labels"]["lab-panel"])
    result = store.request(
        "GET", f"Observation?_id={panel['id']}&_include=Observation:has-member"
    )
    assert {e["fullUrl"] for e in result["body"]["entry"]} == {
        ref(panel),
        *[r["reference"] for r in panel["hasMember"]],
    }
    qr = next(r for r in resources if ref(r) == case["labels"]["intake-response"])
    i = qr["identifier"]
    assert (
        store.request(
            "GET", f"QuestionnaireResponse?identifier={i['system']}|{i['value']}"
        )["body"]["total"]
        == 1
    )


def test_shared_entities_and_environment_profiles(pipeline):
    _, root, _, _ = pipeline
    plans = load_jsonl(root / "generation/episodes.jsonl")
    same = [
        p
        for p in plans
        if p["shard"] == plans[0]["shard"]
        and p["plan"]["site"] == plans[0]["plan"]["site"]
    ]
    assert len(same) > 1
    assert len({p["labels"]["clinic"] for p in same}) == 1
    tasks = load_jsonl(root / "train/tasks.jsonl")
    for profile in ("raw", "assisted"):
        env = load_environment(
            data_dir=str(root),
            family="record_transfer_transmission",
            tool_profile=profile,
            max_examples=2,
        )
        assert len(env.dataset) == 2
    assert Counter(t["split"] for t in tasks) == {"train": len(tasks)}


@pytest.mark.parametrize("failure", ["list_dates", "team_role"])
def test_independent_validator_regressions(pipeline, failure):
    _, root, _, _ = pipeline
    case = load_jsonl(root / "generation/episodes.jsonl")[0]
    resources = load_jsonl(root / case["shard"])
    labels = case["labels"]
    index = {ref(r): r for r in resources}
    if failure == "list_dates":
        index[labels["transfer-list"]]["entry"][0]["date"] = case["plan"][
            "snapshot_date"
        ]
        message = "entry dates"
    else:
        index[labels["coordination-team"]]["participant"][0]["member"] = {
            "reference": labels["clinician-role"]
        }
        message = "requires a Practitioner"
    with pytest.raises(ValueError, match=message):
        validate_graph(resources)
