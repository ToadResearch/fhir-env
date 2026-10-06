"""Conditional matching is independent of search-result presentation controls."""

import copy

import pytest

from fhir_workflows.store import FhirStore


def setup_case(existing_count):
    body = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": {"reference": "Patient/subject"},
        "identifier": [
            {"system": "https://example.invalid/workflow", "value": "retry"}
        ],
    }
    resources = [
        {"resourceType": "Patient", "id": "subject", "meta": {"versionId": "1"}},
        *[
            {**copy.deepcopy(body), "id": f"existing-{i}", "meta": {"versionId": "1"}}
            for i in range(existing_count)
        ],
    ]
    return body, FhirStore(resources, "Patient/subject", ["Task"])


@pytest.mark.parametrize("existing_count", [1, 2, 12])
@pytest.mark.parametrize(
    "control",
    [
        "_page=1",
        "_count=1",
        "_page=1&_count=1",
        "_include=Task:focus",
        "_sort=_id",
        "_summary=count",
        "_elements=id",
    ],
)
def test_controls_cannot_create_duplicates_or_hide_ambiguous_matches(
    existing_count, control
):
    body, store = setup_case(existing_count)
    before = copy.deepcopy(store.resources)
    result = store.request(
        "POST", "Task", body, {"If-None-Exist": "identifier=retry&" + control}
    )
    assert result["status"] == 400
    assert store.resources == before and store.audit == []


@pytest.mark.parametrize(
    "existing_count,expected_status", [(0, 201), (1, 200), (2, 412), (12, 412)]
)
def test_conditional_create_uses_full_match_cardinality(
    existing_count, expected_status
):
    body, store = setup_case(existing_count)
    result = store.request("POST", "Task", body, {"If-None-Exist": "identifier=retry"})
    assert result["status"] == expected_status
    assert len(store.resources) == 1 + max(1, existing_count)
    assert len(store.audit) == int(existing_count == 0)
    if existing_count == 1:
        assert result["body"]["id"] == "existing-0"


def test_id_filter_is_allowed_and_empty_condition_is_rejected():
    body, store = setup_case(1)
    before = copy.deepcopy(store.resources)
    assert (
        store.request("POST", "Task", body, {"If-None-Exist": "_id=existing-0"})[
            "status"
        ]
        == 200
    )
    assert store.request("POST", "Task", body, {"If-None-Exist": ""})["status"] == 400
    assert store.resources == before and store.audit == []
