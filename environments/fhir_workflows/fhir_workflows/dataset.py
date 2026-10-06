"""Source-preserving import, separately labelled synthetic episodes and task compiler."""

import base64
import hashlib
import json
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from .validation import ref, validate_graph

VERSION = "0.3.0"
MRN_SYSTEM = "https://fhir-workflows.example/mrn"
LOCAL_SYSTEM = "https://fhir-workflows.example/codes"
LOINC = "http://loinc.org"
ORIGIN_SYSTEM = "https://fhir-workflows.example/origin"


def stable_id(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def cc(code, text=None, system=LOCAL_SYSTEM):
    return {
        "coding": [{"system": system, "code": code}],
        **({"text": text} if text else {}),
    }


def reference(resource):
    return {"reference": ref(resource)}


def annotate_dependencies(task):
    """Template-defined information DAG, independent of query packing/call count."""
    patient = [("supplied_mrn", "patient")]
    family = task["family"]
    graphs = {
        "latest_result": [("supplied_patient", "observation")],
        "coverage_lookup": patient + [("patient", "coverage")],
        "coverage_change": patient + [("patient", "coverage")],
        "linked_report": patient
        + [("patient", "order"), ("order", "report"), ("report", "observation")],
        "message_followup": patient
        + [
            ("patient", "communication"),
            ("patient", "order"),
            ("order", "report"),
            ("report", "observation"),
        ],
        "cancel_referral": patient
        + [("patient", "order"), ("order", "fulfilment_task")],
        "delete_duplicate_draft": [("supplied_patient", "duplicate_task")],
        "document_availability": patient + [("patient", "document_search")],
        "prior_auth_evidence": patient
        + [
            ("supplied_policy", "policy"),
            ("policy", "service"),
            ("patient", "service"),
            ("policy", "procedure"),
            ("patient", "procedure"),
            ("policy", "document_search"),
            ("patient", "document_search"),
        ],
        "correct_erroneous_result": [("supplied_resource", "observation")],
        "documented_home_medications": patient + [("patient", "medication_search")],
        "documented_conditions": patient + [("patient", "condition_search")],
        "ambiguous_identity": [("supplied_name", "candidate_search")],
        "longitudinal_lab_trend": patient + [("patient", "serial_observations")],
        "medication_reconciliation": patient + [("patient", "reported_discrepancy")],
    }
    edges = task.get("dependency_edges") or graphs[family]
    levels = {a: 0 for a, b in edges if a.startswith("supplied_")}
    remaining = list(edges)
    while remaining:
        available = [(a, b) for a, b in remaining if a in levels]
        if not available:
            raise ValueError("Cyclic template dependency graph")
        for a, b in available:
            levels[b] = max(levels.get(b, 0), levels[a] + 1)
            remaining.remove((a, b))
    depth = max(levels.values())
    task["retrieval_dependencies"] = [{"from": a, "to": b} for a, b in edges]
    task["dependency_depth"] = depth
    task["workflow_depth"] = depth + int(
        bool(
            task["gold"]["updates"]
            or task["gold"]["creates"]
            or task["gold"]["deletes"]
        )
    )
    task["depth_basis"] = (
        "Template-declared prerequisite DAG, not a proven minimal-call plan"
    )


def common(kind, identity, origin, **fields):
    return {
        "resourceType": kind,
        "id": stable_id(kind, identity),
        "meta": {"versionId": "1", "tag": [{"system": ORIGIN_SYSTEM, "code": origin}]},
        **fields,
    }


def read_source(path):
    con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    assignments = defaultdict(set)
    for row in con.execute(
        "SELECT DISTINCT patient_id, split FROM benchmark_ground_truth WHERE patient_id IS NOT NULL"
    ):
        assignments[row["patient_id"]].add(row["split"])
    if any(len(s) != 1 for s in assignments.values()):
        raise ValueError("Source patient has conflicting split labels")
    train_ids = sorted(
        (p for p, s in assignments.items() if s == {"train"}),
        key=lambda p: stable_id("development", p),
    )
    dev = set(train_ids[: max(1, len(train_ids) // 10)])
    records = []
    for row in con.execute(
        "SELECT patient_id, profile, age, sex FROM longitudinal_patients ORDER BY patient_id"
    ):
        source_id = row["patient_id"]
        if source_id not in assignments:
            raise ValueError(f"Patient {source_id} lacks source split")
        upstream = next(iter(assignments[source_id]))
        split = "dev" if source_id in dev else upstream
        encounters = [
            dict(e)
            for e in con.execute(
                "SELECT encounter_id, encounter_date, encounter_type, attending_name, chief_complaint, note_text FROM longitudinal_encounters WHERE patient_id=? ORDER BY encounter_date, encounter_id",
                (source_id,),
            )
        ]
        records.append(
            {
                "source_id": source_id,
                "profile": json.loads(row["profile"]),
                "age": row["age"],
                "sex": row["sex"],
                "upstream_split": upstream,
                "split": split,
                "encounters": encounters,
            }
        )
    con.close()
    return records


def demo_source():
    records = []
    for i in range(16):
        records.append(
            {
                "source_id": f"demo-{i}",
                "profile": {
                    "chronic_conditions": ["Fictional example condition"]
                    if i % 2
                    else [],
                    "allergies": [],
                    "home_medications": [
                        {
                            "name": "Fictional reported medication",
                            "dose": "Reported once daily",
                        }
                    ]
                    if i % 2
                    else [],
                },
                "age": 35 + i,
                "sex": "F" if i % 2 else "M",
                "upstream_split": "demo",
                "split": "dev" if i >= 12 else "train",
                "encounters": [
                    {
                        "encounter_id": f"demo-enc-{i}",
                        "encounter_date": "2025-02-03",
                        "encounter_type": "outpatient",
                        "attending_name": "Synthetic Practitioner",
                        "chief_complaint": "Synthetic administrative follow-up",
                        "note_text": "Fictional infrastructure test chart. No patient care is represented.",
                    }
                ],
            }
        )
    return records


def make_world(source, pair_name, seed):
    sid = source["source_id"]
    rng = random.Random(int(stable_id(seed, sid), 16))
    resources, ledger, handles = [], [], {}

    def add(label, kind, origin="generated-workflow", source_key=None, **fields):
        r = common(kind, (sid, label), origin, **fields)
        resources.append(r)
        handles[label] = r
        ledger.append(
            {
                "resource_ref": ref(r),
                "origin": origin,
                "source_patient_id": sid,
                "source_key": source_key,
                "generator_version": VERSION,
                "seed": seed,
            }
        )
        return r

    last = (
        max(date.fromisoformat(e["encounter_date"][:10]) for e in source["encounters"])
        if source["encounters"]
        else date(2025, 1, 1)
    )
    first = (
        min(date.fromisoformat(e["encounter_date"][:10]) for e in source["encounters"])
        if source["encounters"]
        else last
    )
    snapshot = last + timedelta(days=60)
    birth = first - timedelta(days=365 * max(0, source["age"]) + rng.randint(1, 300))
    mrn = "SYN-" + stable_id("mrn", sid)[:10]
    patient = add(
        "patient",
        "Patient",
        "source-with-generated-demographics",
        source_key="longitudinal_patients.profile",
        identifier=[{"system": MRN_SYSTEM, "value": mrn}],
        name=[
            {
                "text": pair_name,
                "family": pair_name.split()[-1],
                "given": [pair_name.split()[0]],
            }
        ],
        gender={"F": "female", "M": "male"}.get(source["sex"], "unknown"),
        birthDate=birth.isoformat(),
        active=True,
    )
    organization = add(
        "organization", "Organization", name="Synthetic Community Clinic", active=True
    )
    payer = add(
        "payer", "Organization", name="Fictional Benchmark Health Plan", active=True
    )
    provider = add(
        "practitioner",
        "Practitioner",
        name=[{"text": "Synthetic Coordinator"}],
        active=True,
    )
    location = add(
        "location", "Location", name="Synthetic Outpatient Office", status="active"
    )
    add(
        "role",
        "PractitionerRole",
        active=True,
        practitioner=reference(provider),
        organization=reference(organization),
        location=[reference(location)],
    )
    add(
        "caregiver",
        "RelatedPerson",
        patient=reference(patient),
        name=[{"text": "Synthetic Contact"}],
        active=True,
    )
    for e in source["encounters"]:
        kind = {"inpatient": "IMP", "icu": "IMP", "ed": "EMER"}.get(
            e["encounter_type"], "AMB"
        )
        encounter = add(
            f"enc-{e['encounter_id']}",
            "Encounter",
            "source-with-generated-provider",
            source_key=f"longitudinal_encounters/{e['encounter_id']}",
            status="finished",
            **{
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": kind,
                }
            },
            subject=reference(patient),
            period={"start": e["encounter_date"][:10]},
            type=[{"text": e["encounter_type"]}],
        )
        add(
            f"note-{e['encounter_id']}",
            "DocumentReference",
            "source-preserved",
            source_key=f"longitudinal_encounters/{e['encounter_id']}/note_text",
            status="current",
            subject=reference(patient),
            context={"encounter": [reference(encounter)]},
            content=[
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "data": base64.b64encode(e["note_text"].encode()).decode(),
                        "title": "Source clinical note",
                    }
                }
            ],
        )
    profile = source["profile"]
    add(
        "source-profile",
        "DocumentReference",
        "source-preserved",
        source_key="longitudinal_patients.profile",
        status="current",
        subject=reference(patient),
        content=[
            {
                "attachment": {
                    "contentType": "application/json",
                    "data": base64.b64encode(
                        json.dumps(profile, sort_keys=True).encode()
                    ).decode(),
                    "title": "Exact source profile",
                }
            }
        ],
    )
    for i, condition in enumerate(profile.get("chronic_conditions", [])):
        add(
            f"condition-{i}",
            "Condition",
            "source-profile",
            source_key=f"profile/chronic_conditions/{i}",
            subject=reference(patient),
            code={"text": str(condition)},
        )
    for i, allergy in enumerate(profile.get("allergies", [])):
        if str(allergy).strip().lower() in {
            "nkda",
            "none",
            "no known allergies",
            "no known drug allergies",
            "nka",
        }:
            continue  # the exact negative statement remains in the source-profile document
        add(
            f"allergy-{i}",
            "AllergyIntolerance",
            "source-profile",
            source_key=f"profile/allergies/{i}",
            patient=reference(patient),
            code={"text": str(allergy)},
        )
    for i, medication in enumerate(profile.get("home_medications", [])):
        text = (
            medication.get("name", "Reported home medication")
            if isinstance(medication, dict)
            else str(medication)
        )
        dose = medication.get("dose") if isinstance(medication, dict) else None
        add(
            f"medication-{i}",
            "MedicationStatement",
            "source-profile",
            source_key=f"profile/home_medications/{i}",
            status="active",
            subject=reference(patient),
            medicationCodeableConcept={"text": text},
            **({"dosage": [{"text": str(dose)}]} if dose else {}),
        )
    encounter = add(
        "workflow-encounter",
        "Encounter",
        status="finished",
        **{
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
            }
        },
        subject=reference(patient),
        period={"start": (snapshot - timedelta(days=10)).isoformat()},
        type=[{"text": "Fictional benchmark workflow"}],
    )
    request = add(
        "lab-order",
        "ServiceRequest",
        status="completed",
        intent="order",
        code=cc("718-7", "Hemoglobin", LOINC),
        subject=reference(patient),
        encounter=reference(encounter),
        authoredOn=(snapshot - timedelta(days=10)).isoformat(),
        identifier=[
            {
                "system": LOCAL_SYSTEM + "/orders",
                "value": "ORD-" + stable_id(sid, "lab")[:10],
            }
        ],
    )
    specimen = add(
        "specimen",
        "Specimen",
        status="available",
        subject=reference(patient),
        request=[reference(request)],
        collection={"collectedDateTime": (snapshot - timedelta(days=8)).isoformat()},
    )
    hemoglobin = round(rng.uniform(11, 15), 1)
    result = add(
        "lab-result",
        "Observation",
        status="final",
        code=cc("718-7", "Hemoglobin", LOINC),
        subject=reference(patient),
        encounter=reference(encounter),
        basedOn=[reference(request)],
        specimen=reference(specimen),
        effectiveDateTime=(snapshot - timedelta(days=8)).isoformat(),
        valueQuantity={
            "value": hemoglobin,
            "unit": "g/dL",
            "system": "http://unitsofmeasure.org",
            "code": "g/dL",
        },
    )
    add(
        "old-result",
        "Observation",
        status="final",
        code=cc("718-7", "Hemoglobin", LOINC),
        subject=reference(patient),
        effectiveDateTime=(snapshot - timedelta(days=300)).isoformat(),
        valueQuantity={
            "value": 13.0,
            "unit": "g/dL",
            "system": "http://unitsofmeasure.org",
            "code": "g/dL",
        },
    )
    add(
        "lab-report",
        "DiagnosticReport",
        status="final",
        code=cc("718-7", "Hemoglobin report", LOINC),
        subject=reference(patient),
        basedOn=[reference(request)],
        effectiveDateTime=(snapshot - timedelta(days=8)).isoformat(),
        result=[reference(result)],
    )
    referral = add(
        "referral",
        "ServiceRequest",
        status="active",
        intent="order",
        subject=reference(patient),
        code=cc("specialty-review", "Fictional specialty review"),
        authoredOn=snapshot.isoformat(),
        identifier=[
            {
                "system": LOCAL_SYSTEM + "/orders",
                "value": "REF-" + stable_id(sid, "referral")[:10],
            }
        ],
    )
    add(
        "referral-task",
        "Task",
        status="requested",
        intent="order",
        **{"for": reference(patient)},
        focus=reference(referral),
        description="Coordinate the fictional referral",
    )
    add(
        "coverage",
        "Coverage",
        status="active",
        beneficiary=reference(patient),
        payor=[reference(payer)],
        subscriberId="OLD-" + stable_id(sid, "coverage")[:8],
        period={"start": (snapshot - timedelta(days=400)).isoformat()},
    )
    add(
        "expired-coverage",
        "Coverage",
        status="cancelled",
        beneficiary=reference(patient),
        payor=[reference(payer)],
        subscriberId="EXPIRED-" + stable_id(sid)[:8],
        period={
            "start": (snapshot - timedelta(days=800)).isoformat(),
            "end": (snapshot - timedelta(days=401)).isoformat(),
        },
    )
    add(
        "draft-keep",
        "Task",
        status="draft",
        intent="order",
        **{"for": reference(patient)},
        description="Draft administrative callback",
        identifier=[
            {"system": LOCAL_SYSTEM + "/drafts", "value": "KEEP-" + stable_id(sid)[:8]}
        ],
    )
    draft = add(
        "draft-delete",
        "Task",
        status="draft",
        intent="order",
        **{"for": reference(patient)},
        description="Erroneous duplicate administrative callback",
        identifier=[
            {"system": LOCAL_SYSTEM + "/drafts", "value": "DUP-" + stable_id(sid)[:8]}
        ],
    )
    add(
        "message",
        "Communication",
        status="completed",
        subject=reference(patient),
        sent=snapshot.isoformat(),
        payload=[
            {
                "contentString": "Patient requests a copy of the available hemoglobin result; a previous outside study may not be in this chart."
            }
        ],
    )
    add(
        "appointment",
        "Appointment",
        status="booked",
        start=(snapshot + timedelta(days=7)).isoformat() + "T14:00:00Z",
        end=(snapshot + timedelta(days=7)).isoformat() + "T14:30:00Z",
        participant=[{"actor": reference(patient), "status": "accepted"}],
    )
    goal = add(
        "goal",
        "Goal",
        lifecycleStatus="active",
        subject=reference(patient),
        description={"text": "Complete requested follow-up documentation"},
    )
    add(
        "careplan",
        "CarePlan",
        status="active",
        intent="plan",
        subject=reference(patient),
        goal=[reference(goal)],
        description="Fictional benchmark follow-up coordination",
    )
    add(
        "medication-order",
        "MedicationRequest",
        status="draft",
        intent="proposal",
        subject=reference(patient),
        medicationCodeableConcept={
            "text": "Fictional test medication; no treatment decision implied"
        },
    )
    prerequisite_days = rng.choice([25, 25, 180])
    add(
        "prerequisite",
        "Procedure",
        status="completed",
        subject=reference(patient),
        code=cc("prerequisite-review", "Fictional prerequisite review"),
        performedDateTime=(snapshot - timedelta(days=prerequisite_days)).isoformat(),
    )
    questionnaire = add(
        "policy",
        "Questionnaire",
        status="active",
        title="Fictional evidence checklist v1; not a payer policy",
        item=[
            {
                "linkId": "request",
                "text": "Requested specialty review is documented",
                "type": "boolean",
            },
            {
                "linkId": "prerequisite",
                "text": "Prerequisite review documented within 90 days",
                "type": "boolean",
            },
            {
                "linkId": "external-study",
                "text": "Outside study documentation available",
                "type": "boolean",
            },
        ],
    )
    add(
        "questionnaire-draft",
        "QuestionnaireResponse",
        status="in-progress",
        questionnaire="https://fhir-workflows.example/Questionnaire/"
        + questionnaire["id"],
        subject=reference(patient),
    )
    outside = add(
        "outside-study",
        "DocumentReference",
        status="current",
        subject=reference(patient),
        type=cc("external-study"),
        content=[
            {
                "attachment": {
                    "contentType": "text/plain",
                    "data": base64.b64encode(
                        b"Fictional outside study documentation."
                    ).decode(),
                    "title": "Outside study",
                }
            }
        ],
    )
    from .longitudinal import expand_chart

    # Source age belongs to the beginning of a potentially multi-year history.
    # Preserve that source profile verbatim; use the generated DOB to age the
    # synthetic extension consistently at its own snapshot.
    workflow_source = {
        **source,
        "age": (snapshot - birth).days / 365.2425,
    }
    longitudinal = expand_chart(workflow_source, handles, snapshot, rng, add)
    from .scenarios import expand_workflows

    workflows = expand_workflows(workflow_source, handles, snapshot, rng, add)
    # Gold never hides in these resources. Provenance describes origin, not correctness.
    for entry in list(ledger):
        if entry["resource_ref"] in {ref(draft), *workflows["deletable"]}:
            continue  # explicit unreferenced DELETE fixture
        resources.append(
            common(
                "Provenance",
                (sid, entry["resource_ref"]),
                "generated-lineage",
                target=[{"reference": entry["resource_ref"]}],
                recorded=snapshot.isoformat() + "T12:00:00Z",
                agent=[{"who": reference(organization)}],
                activity={"text": entry["origin"]},
            )
        )
    return (
        resources,
        ledger,
        handles,
        {
            "snapshot": snapshot.isoformat(),
            "mrn": mrn,
            "hemoglobin": hemoglobin,
            "outside": ref(outside),
            "outside_available": rng.choice([True, False]),
            "prerequisite_recent": prerequisite_days <= 90,
            "longitudinal": longitudinal,
            "workflows": workflows,
        },
    )


def compile_tasks(source, handles, facts):
    p = handles["patient"]
    pid = p["id"]
    mrn = facts["mrn"]
    at = facts["snapshot"]
    h = handles
    tasks = []

    def step(method, path, body=None, headers=None):
        return {
            "method": method,
            "path": path,
            **({"body": body} if body is not None else {}),
            **({"headers": headers} if headers else {}),
        }

    lookup = step("GET", "Patient?identifier=" + MRN_SYSTEM + "|" + mrn)

    def add(
        family,
        depth,
        request,
        answer,
        evidence,
        actions,
        updates=(),
        creates=(),
        deletes=(),
        omit=(),
        evidence_fields=None,
    ):
        writable = sorted(
            {u["ref"].split("/")[0] for u in updates}
            | {c["resourceType"] for c in creates}
            | {d.split("/")[0] for d in deletes}
        )
        task = {
            "id": stable_id(source["source_id"], family),
            "patient_id": pid,
            "split": source["split"],
            "upstream_split": source["upstream_split"],
            "family": family,
            "dependency_depth": depth,
            "reference_calls": len(actions),
            "snapshot_date": at,
            "prompt": request
            + "\nReturn JSON with exactly two keys: answer (the requested structured fields) and evidence (FHIR Type/id references actually retrieved).",
            "gold": {
                "answer": answer,
                "evidence": sorted(evidence),
                "evidence_fields": evidence_fields or {},
                "updates": list(updates),
                "creates": list(creates),
                "deletes": list(deletes),
            },
            "writable_types": writable,
            "delete_refs": list(deletes),
            "omit": list(omit),
            "reference_steps": actions,
        }
        tasks.append(task)

    r = h["lab-result"]
    report = h["lab-report"]
    order = h["lab-order"]
    add(
        "latest_result",
        1,
        f"For known patient {ref(p)}, return the most recent documented final hemoglobin (LOINC 718-7) as of {at}. Answer fields: value and unit.",
        {"value": facts["hemoglobin"], "unit": "g/dL"},
        [ref(r)],
        [
            step(
                "GET",
                f"Observation?patient={pid}&code={LOINC}|718-7&status=final&date=le{at}&_sort=-date&_count=1",
            )
        ],
        evidence_fields={ref(r): ["valueQuantity", "effectiveDateTime"]},
    )
    add(
        "coverage_lookup",
        2,
        f"The caller gives MRN {mrn} (system {MRN_SYSTEM}). Find the currently active insurance. Answer fields: subscriber_id and status.",
        {"subscriber_id": h["coverage"]["subscriberId"], "status": "active"},
        [ref(p), ref(h["coverage"])],
        [lookup, step("GET", f"Coverage?patient={pid}&status=active")],
        evidence_fields={
            ref(p): ["identifier"],
            ref(h["coverage"]): ["subscriberId", "status"],
        },
    )
    add(
        "linked_report",
        3,
        f"For MRN {mrn}, find final hemoglobin report linked to completed order identifier {order['identifier'][0]['value']}. Return value and unit from its linked result.",
        {"value": facts["hemoglobin"], "unit": "g/dL"},
        [ref(p), ref(order), ref(report), ref(r)],
        [
            lookup,
            step(
                "GET",
                f"ServiceRequest?patient={pid}&identifier={order['identifier'][0]['value']}",
            ),
            step(
                "GET",
                f"DiagnosticReport?based-on={ref(order)}&_include=DiagnosticReport:result",
            ),
        ],
        evidence_fields={
            ref(r): ["valueQuantity"],
            ref(order): ["identifier"],
            ref(report): ["result"],
        },
    )
    add(
        "message_followup",
        4,
        f"Find the patient with MRN {mrn}; read their Communication sent on {at}, resolve the completed hemoglobin order, and retrieve its final report and linked result. Create one requested Task with intent order, for this patient, focus on that report, and description 'Result copy requested'. Answer fields: task_created=true, value and unit. Do not interpret the result.",
        {"task_created": True, "value": facts["hemoglobin"], "unit": "g/dL"},
        [ref(p), ref(h["message"]), ref(order), ref(report), ref(r)],
        [
            lookup,
            step("GET", f"Communication?patient={pid}&status=completed"),
            step(
                "GET",
                f"ServiceRequest?patient={pid}&identifier={order['identifier'][0]['value']}",
            ),
            step(
                "GET",
                f"DiagnosticReport?based-on={ref(order)}&_include=DiagnosticReport:result",
            ),
            step(
                "POST",
                "Task",
                {
                    "resourceType": "Task",
                    "status": "requested",
                    "intent": "order",
                    "for": reference(p),
                    "focus": reference(report),
                    "description": "Result copy requested",
                },
            ),
        ],
        creates=[
            {
                "resourceType": "Task",
                "status": "requested",
                "intent": "order",
                "for": reference(p),
                "focus": reference(report),
                "description": "Result copy requested",
            }
        ],
        evidence_fields={ref(r): ["valueQuantity"], ref(h["message"]): ["payload"]},
    )
    referral = h["referral"]
    task = h["referral-task"]
    referral_new = {**referral, "status": "revoked"}
    task_new = {**task, "status": "cancelled"}
    add(
        "cancel_referral",
        3,
        f"For MRN {mrn}, cancel the active referral with identifier {referral['identifier'][0]['value']} and its requested fulfilment Task. Apply both changes together in one transaction: ServiceRequest.status=revoked, Task.status=cancelled. Preserve every other field. Answer fields: cancelled=true.",
        {"cancelled": True},
        [ref(p), ref(referral), ref(task)],
        [
            lookup,
            step(
                "GET",
                f"ServiceRequest?patient={pid}&identifier={referral['identifier'][0]['value']}",
            ),
            step("GET", f"Task?focus={ref(referral)}&status=requested"),
            step(
                "POST",
                "",
                {
                    "resourceType": "Bundle",
                    "type": "transaction",
                    "entry": [
                        {
                            "resource": referral_new,
                            "request": {
                                "method": "PUT",
                                "url": ref(referral),
                                "ifMatch": 'W/"1"',
                            },
                        },
                        {
                            "resource": task_new,
                            "request": {
                                "method": "PUT",
                                "url": ref(task),
                                "ifMatch": 'W/"1"',
                            },
                        },
                    ],
                },
            ),
        ],
        updates=[
            {"ref": ref(referral), "field": "status", "value": "revoked"},
            {"ref": ref(task), "field": "status", "value": "cancelled"},
        ],
    )
    tasks[-1]["requires_transaction"] = True
    coverage = h["coverage"]
    coverage_new = {
        **coverage,
        "status": "cancelled",
        "period": {**coverage["period"], "end": at},
    }
    new = {
        "resourceType": "Coverage",
        "status": "active",
        "beneficiary": reference(p),
        "payor": coverage["payor"],
        "subscriberId": "NEW-" + stable_id(source["source_id"], "new")[:8],
        "period": {"start": at},
    }
    add(
        "coverage_change",
        3,
        f"Caller MRN {mrn} requests an insurance transition effective {at}. Read the active coverage; mark it cancelled with period.end={at}. Create one active Coverage for the same beneficiary/payor with subscriberId={new['subscriberId']} and period.start={at}. Commit together and preserve other fields. Answer fields: updated=true.",
        {"updated": True},
        [ref(p), ref(coverage)],
        [
            lookup,
            step("GET", f"Coverage?patient={pid}&status=active"),
            step(
                "POST",
                "",
                {
                    "resourceType": "Bundle",
                    "type": "transaction",
                    "entry": [
                        {
                            "resource": coverage_new,
                            "request": {
                                "method": "PUT",
                                "url": ref(coverage),
                                "ifMatch": 'W/"1"',
                            },
                        },
                        {
                            "resource": new,
                            "request": {"method": "POST", "url": "Coverage"},
                        },
                    ],
                },
            ),
        ],
        updates=[
            {"ref": ref(coverage), "field": "status", "value": "cancelled"},
            {"ref": ref(coverage), "field": "period", "value": coverage_new["period"]},
        ],
        creates=[new],
    )
    tasks[-1]["requires_transaction"] = True
    draft = h["draft-delete"]
    add(
        "delete_duplicate_draft",
        2,
        f"For known patient {ref(p)}, delete only the erroneous unreferenced draft Task identified by {draft['identifier'][0]['value']}. Keep the other draft and all clinical history. Answer fields: deleted=true.",
        {"deleted": True},
        [ref(draft)],
        [
            step(
                "GET",
                f"Task?patient={pid}&identifier={draft['identifier'][0]['value']}",
            ),
            step("DELETE", ref(draft), headers={"If-Match": 'W/"1"'}),
        ],
        deletes=[ref(draft)],
    )
    outside = h["outside-study"]
    # Hiding a document also hides its lineage so a reference does not advertise hidden facts.
    omit = [
        ref(outside),
        ref(
            common(
                "Provenance", (source["source_id"], ref(outside)), "generated-lineage"
            )
        ),
    ]
    available = facts["outside_available"]
    add(
        "document_availability",
        2,
        f"For MRN {mrn}, check whether an external-study DocumentReference is available in this accessible chart. Return documented=false when no matching resource exists; do not assert that the study never occurred. Answer field: documented.",
        {"documented": available},
        [ref(p)] + ([ref(outside)] if available else []),
        [lookup, step("GET", f"DocumentReference?patient={pid}&type=external-study")],
        omit=[] if available else omit,
    )
    if not available:
        tasks[-1]["absence_scope"] = {
            "resourceType": "DocumentReference",
            "patient": ref(p),
            "code": "external-study",
        }
    # type is deliberately supported for DocumentReference in the published profile.
    policy = h["policy"]
    prerequisite = h["prerequisite"]
    add(
        "prior_auth_evidence",
        3,
        f"For MRN {mrn}, collect evidence for the fictional Questionnaire {ref(policy)} as of {at}. Return requested_service=true if specialty-review is documented, prerequisite=true if prerequisite-review is documented within 90 days, external_study=false if unavailable. Read the documented prerequisite date even when it is too old. This is a documentation checklist, not a coverage decision. Answer fields: requested_service, prerequisite, external_study.",
        {
            "requested_service": True,
            "prerequisite": facts["prerequisite_recent"],
            "external_study": available,
        },
        [ref(p), ref(policy), ref(referral), ref(prerequisite)]
        + ([ref(outside)] if available else []),
        [
            lookup,
            step("GET", ref(policy)),
            step("GET", f"ServiceRequest?patient={pid}&code=specialty-review"),
            step("GET", f"Procedure?patient={pid}&code=prerequisite-review"),
            step("GET", f"DocumentReference?patient={pid}&type=external-study"),
        ],
        omit=[] if available else omit,
        evidence_fields={
            ref(policy): ["item"],
            ref(prerequisite): ["performedDateTime"],
            ref(referral): ["code"],
        },
    )
    if not available:
        tasks[-1]["absence_scope"] = {
            "resourceType": "DocumentReference",
            "patient": ref(p),
            "code": "external-study",
        }
    add(
        "correct_erroneous_result",
        1,
        f"For known patient {ref(p)}, the operator explicitly identifies Observation {ref(r)} as entered for the wrong sample. Read it and set only status=entered-in-error. Preserve its value, references, and history; do not delete it. Answer fields: corrected=true.",
        {"corrected": True},
        [ref(r)],
        [
            step("GET", ref(r)),
            step(
                "PUT",
                ref(r),
                {**r, "status": "entered-in-error"},
                {"If-Match": 'W/"1"'},
            ),
        ],
        updates=[{"ref": ref(r), "field": "status", "value": "entered-in-error"}],
    )
    for family, kind, labels in [
        (
            "documented_home_medications",
            "MedicationStatement",
            [
                x
                for key, x in h.items()
                if key.startswith("medication-")
                and x["resourceType"] == "MedicationStatement"
            ],
        ),
        (
            "documented_conditions",
            "Condition",
            [x for key, x in h.items() if key.startswith("condition-")],
        ),
    ]:
        if len(labels) > 50:
            raise ValueError(
                "Reference strategy needs pagination for this source profile"
            )
        names = sorted(
            x["medicationCodeableConcept"]["text"]
            if kind == "MedicationStatement"
            else x["code"]["text"]
            for x in labels
        )
        field = "medicationCodeableConcept" if kind == "MedicationStatement" else "code"
        add(
            family,
            2,
            f"For MRN {mrn}, list the names in imported source-profile {kind} records. Return names as a sorted string array. If none are documented in this import, return an empty array; do not infer that the patient has no medications or conditions.",
            {"names": names},
            [ref(p)] + [ref(x) for x in labels],
            [lookup, step("GET", f"{kind}?patient={pid}&_count=50")],
            evidence_fields={ref(x): [field] for x in labels},
        )
        if not labels:
            tasks[-1]["absence_scope"] = {"resourceType": kind, "patient": ref(p)}
    series = facts["longitudinal"]["trend_results"]
    start = facts["longitudinal"]["trend_start"]
    add(
        "longitudinal_lab_trend",
        2,
        f"For MRN {mrn}, retrieve all documented final hemoglobin results (LOINC 718-7) from {start} through {at}, oldest first. Report the recorded measurements; do not infer a diagnosis or treatment response. Answer field: results, an array of objects with date, value and unit.",
        {
            "results": [
                {
                    "date": r["effectiveDateTime"],
                    "value": r["valueQuantity"]["value"],
                    "unit": r["valueQuantity"]["unit"],
                }
                for r in series
            ]
        },
        [ref(p)] + [ref(r) for r in series],
        [
            lookup,
            step(
                "GET",
                f"Observation?patient={pid}&code={LOINC}|718-7&status=final&date=ge{start}&date=le{at}&_sort=date&_count=50",
            ),
        ],
        evidence_fields={
            ref(r): ["effectiveDateTime", "valueQuantity"] for r in series
        },
    )
    discrepancy = facts["longitudinal"]["discrepancy"]
    discrepancy_identifier = discrepancy["identifier"][0]["value"]
    review = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": reference(p),
        "focus": reference(discrepancy),
        "description": "Medication reconciliation requested; patient-reported discrepancy remains unverified.",
    }
    add(
        "medication_reconciliation",
        2,
        f"For MRN {mrn}, locate patient message identifier {discrepancy_identifier}. The patient reports a medication discrepancy but cannot confirm the outside instructions. Create exactly one Task with status=requested, intent=order, for=the resolved patient, focus=that Communication, and description='Medication reconciliation requested; patient-reported discrepancy remains unverified.'. Do not alter medication or prescribing records. Answer fields: review_requested=true, medication_list_changed=false.",
        {"review_requested": True, "medication_list_changed": False},
        [ref(p), ref(discrepancy)],
        [
            lookup,
            step(
                "GET",
                f"Communication?patient={pid}&identifier={discrepancy_identifier}",
            ),
            step("POST", "Task", review),
        ],
        creates=[review],
        evidence_fields={ref(discrepancy): ["payload", "sender", "identifier"]},
    )
    tasks.extend(facts["workflows"]["tasks"])
    for t in tasks:
        t["gold"]["target_evidence"] = [
            key for key in t["gold"]["evidence"] if not key.startswith("Patient/")
        ]
        annotate_dependencies(t)
    return tasks


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def build_dataset(output, source_db=None, seed=17, shard_size=8):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    sources = read_source(source_db) if source_db else demo_source()
    partitions = defaultdict(list)
    for source in sources:
        partitions[source["split"]].append(source)
    all_tasks = []
    all_ledger = []
    resource_counts = Counter()
    patients = Counter()
    depth = Counter()
    for split, records in sorted(partitions.items()):
        split_tasks = []
        for start in range(0, len(records), shard_size):
            shard = f"{split}/shards/{start//shard_size:04d}.ndjson"
            resources = []
            shard_tasks = []
            for j, source in enumerate(records[start : start + shard_size]):
                name = f"Jordan Synthetic{start//shard_size:04d}Pair{j//2}"
                world, ledger, handles, facts = make_world(source, name, seed)
                tasks = compile_tasks(source, handles, facts)
                for task in tasks:
                    task["shard"] = shard
                resources.extend(world)
                all_ledger.extend({**x, "split": split, "shard": shard} for x in ledger)
                shard_tasks.extend(tasks)
                patients[split] += 1
            validate_graph(resources)
            named = defaultdict(list)
            for resource in resources:
                if resource["resourceType"] == "Patient":
                    named[resource["name"][0]["text"]].append(resource)
            for name, candidates in named.items():
                if len(candidates) < 2:
                    continue
                patient = candidates[0]
                evidence = sorted(ref(r) for r in candidates)
                shard_tasks.append(
                    {
                        "id": stable_id(split, shard, name, "ambiguous_identity"),
                        "patient_id": patient["id"],
                        "split": split,
                        "upstream_split": records[start]["upstream_split"],
                        "family": "ambiguous_identity",
                        "dependency_depth": 1,
                        "reference_calls": 1,
                        "snapshot_date": "unspecified",
                        "prompt": f'A caller knows only the name {name} and asks you to cancel their referral. Search for candidate patients. If identity is ambiguous, make no chart change and ask for MRN or exact date of birth. Return JSON with answer={{"action":"clarify","required":["identifier_or_birthdate"]}} and evidence containing the candidate Patient references.',
                        "gold": {
                            "answer": {
                                "action": "clarify",
                                "required": ["identifier_or_birthdate"],
                            },
                            "evidence": evidence,
                            "evidence_fields": {
                                ref(r): ["name", "birthDate", "identifier"]
                                for r in candidates
                            },
                            "updates": [],
                            "creates": [],
                            "deletes": [],
                        },
                        "writable_types": [],
                        "delete_refs": [],
                        "omit": [],
                        "shard": shard,
                        "reference_steps": [
                            {"method": "GET", "path": "Patient?name=" + name}
                        ],
                    }
                )
                annotate_dependencies(shard_tasks[-1])
            resource_counts.update(r["resourceType"] for r in resources)
            depth.update(t["dependency_depth"] for t in shard_tasks)
            write_jsonl(output / shard, resources)
            split_tasks.extend(shard_tasks)
        write_jsonl(output / split / "tasks.jsonl", split_tasks)
        all_tasks.extend(split_tasks)
    write_jsonl(output / "provenance-ledger.jsonl", all_ledger)
    manifest = {
        "version": VERSION,
        "seed": seed,
        "source": "Synthetic Hospital v1.3"
        if source_db
        else "fully artificial infrastructure pilot",
        "source_sha256": hashlib.sha256(Path(source_db).read_bytes()).hexdigest()
        if source_db
        else None,
        "patients": dict(patients),
        "tasks": dict(Counter(t["split"] for t in all_tasks)),
        "families": dict(Counter(t["family"] for t in all_tasks)),
        "resources": dict(resource_counts),
        "dependency_depth": {str(k): v for k, v in depth.items()},
        "domains": dict(Counter(t.get("domain", "legacy") for t in all_tasks)),
        "roles": dict(Counter(t.get("role", "legacy") for t in all_tasks)),
        "variants": dict(
            Counter(
                t.get("domain", "legacy") + ":" + t.get("scenario_variant", "baseline")
                for t in all_tasks
            )
        ),
        "crud": dict(
            Counter(
                "".join(
                    x
                    for x, yes in [
                        ("C", bool(t["gold"]["creates"])),
                        ("R", True),
                        ("U", bool(t["gold"]["updates"])),
                        ("D", bool(t["gold"]["deletes"])),
                    ]
                    if yes
                )
                for t in all_tasks
            )
        ),
        "atomic_tasks": sum(bool(t.get("requires_transaction")) for t in all_tasks),
        "distinct_families": len({t["family"] for t in all_tasks}),
        "validation": "Official FHIR R4 JSON schema + local reference and patient integrity; no full FHIRPath or clinical validation",
        "files": {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*.ndjson")) + sorted(output.rglob("*.jsonl"))
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def load_jsonl(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]
