import copy
from collections import Counter

import pytest

from fhir_query_rl.cli import replay_task
from fhir_query_rl.dataset import MRN_SYSTEM, write_jsonl
from fhir_query_rl.generation_pilot import (
    compile_episode,
    lab_answer,
    make_tasks,
    plan_episode,
    validate_contracts,
    validate_episode,
)
from fhir_query_rl.validation import ref


def patient(age=35):
    return {
        "resourceType": "Patient",
        "id": "pilot-person",
        "meta": {"versionId": "1"},
        "identifier": [{"system": MRN_SYSTEM, "value": "PILOT-TEST"}],
        "birthDate": f"{2025 - age}-01-01",
        "name": [{"family": "Example"}],
        "gender": "unknown",
    }


def compile_case(i=0, age=35):
    p = patient(age)
    plan = plan_episode(p, "2025-10-06", i)
    return p, compile_episode(p, plan)


def test_balanced_plan_is_not_confounded_with_age_band():
    plans = [
        plan_episode(patient((5, 30, 50, 70)[i % 4]), "2025-10-06", i)
        for i in range(24)
    ]
    assert set(Counter(p["lab_branch"] for p in plans).values()) == {6}
    assert set(Counter(p["panel_encoding"] for p in plans).values()) == {8}
    assert len({(p["lab_branch"], p["panel_encoding"]) for p in plans}) == 12
    for band in range(4):
        assert len({p["lab_branch"] for p in plans[band::4]}) == 4


@pytest.mark.parametrize("i", range(24))
def test_all_branches_and_crud_replay(tmp_path, i):
    p, compiled = compile_case(i, age=(5, 30, 50, 70)[i % 4])
    shard = "dev/shards/0000.ndjson"
    write_jsonl(tmp_path / shard, [p, *compiled["resources"]])
    for task in make_tasks(p, compiled, shard):
        assert replay_task(tmp_path, task)["metrics"]["strict_success"] == 1
    assert compiled == compile_episode(p, compiled["plan"])


def test_same_patient_different_branch_changes_chart_derived_answer():
    p, final = compile_case(0)
    pending = compile_episode(p, plan_episode(p, "2025-10-06", 1))
    answers = [
        lab_answer({ref(r): r for r in c["resources"]}, c["labels"])
        for c in (final, pending)
    ]
    assert answers[0]["measurements"] and not answers[1]["measurements"]
    assert (
        answers[0]["report_status"] == "final"
        and answers[1]["report_status"] == "registered"
    )


def test_panel_representations_preserve_measurements():
    p, members = compile_case(0)
    # Different indices; force the same final event plan and measurement values.
    variants = []
    for encoding in ("members", "components", "individual"):
        plan = copy.deepcopy(members["plan"])
        plan["panel_encoding"] = encoding
        compiled = compile_episode(p, plan)
        variants.append(
            lab_answer({ref(r): r for r in compiled["resources"]}, compiled["labels"])
        )
    assert variants[0] == variants[1] == variants[2]


def test_episode_validator_catches_valid_fhir_but_wrong_order_link():
    _, compiled = compile_case(0)
    specimen = next(
        r for r in compiled["resources"] if ref(r) == compiled["labels"]["lab-specimen"]
    )
    specimen["request"] = [{"reference": compiled["labels"]["older-lab-order"]}]
    with pytest.raises(ValueError, match="another laboratory order"):
        validate_episode(compiled)


def test_root_choice_contract_rejects_two_observation_values():
    r = {
        "resourceType": "Observation",
        "id": "bad",
        "status": "final",
        "code": {"text": "Example"},
        "valueString": "two values",
        "valueQuantity": {"value": 1},
    }
    with pytest.raises(ValueError, match="multiple values"):
        validate_contracts([r])


def test_corrections_preserve_prior_versions():
    _, compiled = compile_case(3)
    report = next(
        r for r in compiled["resources"] if ref(r) == compiled["labels"]["lab-report"]
    )
    previous = next(r for r in compiled["history"] if ref(r) == ref(report))
    assert previous["status"] == "final" and previous["meta"]["versionId"] == "1"
    assert report["status"] == "corrected" and report["meta"]["versionId"] == "2"


def test_identifier_cardinality_depends_on_resource_type():
    _, compiled = compile_case()
    by_type = {r["resourceType"]: r for r in compiled["resources"]}
    assert isinstance(by_type["QuestionnaireResponse"]["identifier"], dict)
    assert isinstance(by_type["Questionnaire"]["identifier"], list)
