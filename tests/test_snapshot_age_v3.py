"""Synthetic follow-up ages advance with longitudinal source dates."""

import base64
import copy
import json
from datetime import date

import pytest

from fhir_query_rl.dataset import demo_source, make_world
from fhir_query_rl.validation import ref, validate_graph


@pytest.mark.parametrize("baseline_age", [0, 15])
def test_multi_year_followup_uses_snapshot_age_and_preserves_source(baseline_age):
    source = copy.deepcopy(demo_source()[0])
    source["source_id"] = f"long-pediatric-chart-{baseline_age}"
    source["age"] = baseline_age
    first = {**source["encounters"][0], "encounter_date": "2020-01-01"}
    last = {
        **first,
        "encounter_id": first["encounter_id"] + "-later",
        "encounter_date": "2025-01-01",
    }
    source["encounters"] = [first, last]
    original = copy.deepcopy(source)
    world, ledger, handles, facts = make_world(source, "Synthetic Followup", 17)
    validate_graph(world)
    assert source == original
    birth = date.fromisoformat(handles["patient"]["birthDate"])
    snapshot = date.fromisoformat(facts["snapshot"])
    age_at_snapshot = (snapshot - birth).days / 365.2425
    assert age_at_snapshot >= baseline_age + 5
    weight = handles["followup-weight-2"]["valueQuantity"]["value"]
    assert weight > (15 if baseline_age == 0 else 55)
    expected_band = "pediatric" if age_at_snapshot < 18 else "adult"
    assert all(
        task["age_band"] == expected_band for task in facts["workflows"]["tasks"]
    )
    call = handles["v3-contact-call"]
    expected_sender = (
        handles["caregiver"] if age_at_snapshot < 18 else handles["patient"]
    )
    assert call["sender"]["reference"] == ref(expected_sender)
    preserved = handles["source-profile"]["content"][0]["attachment"]["data"]
    assert json.loads(base64.b64decode(preserved)) == original["profile"]
    entry = next(
        x for x in ledger if x["resource_ref"] == ref(handles["source-profile"])
    )
    assert entry["origin"] == "source-preserved"
    assert entry["source_key"] == "longitudinal_patients.profile"
