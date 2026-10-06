"""Experimental event-to-FHIR compiler; the frozen production corpus is untouched.

FHIR R4 example shapes and definition-derived contracts are in the adjacent
catalog. Events/history/oracles are sidecars, never agent-visible resource fields.
This is a structural/query experiment, not a physiology or clinical simulator.
"""

from __future__ import annotations

import base64
import copy
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .dataset import (
    LOCAL_SYSTEM,
    LOINC,
    MRN_SYSTEM,
    ORIGIN_SYSTEM,
    cc,
    reference,
    stable_id,
)
from .validation import ref, references, schema, validate_graph

PILOT_VERSION = "event-graph-pilot-1"
ID_SYSTEM = "https://fhir-query-rl.example/pilot/identifier"
UCUM = "http://unitsofmeasure.org"
CATALOG_PATH = Path(__file__).with_name("generation_patterns.json")


def catalog():
    return json.loads(CATALOG_PATH.read_text())


def validate_contracts(resources):
    """Definition-derived root cardinalities, choice exclusivity and target types.

    This complements JSON schema; it is deliberately not a full profile validator.
    """
    contracts = catalog()["contracts"]
    for r in resources:
        kind = r["resourceType"]
        contract = contracts.get(kind, {})
        for field in contract.get("required", []):
            if "[x]" in field:
                if not any(k.startswith(field[:-3]) for k in r):
                    raise ValueError(f"{ref(r)}: required choice {field}")
            elif field not in r or r[field] in (None, [], ""):
                raise ValueError(f"{ref(r)}: required {field}")
        for field, types in contract.get("choices", {}).items():
            base = field[:-3]
            allowed = {base + t[0].upper() + t[1:] for t in types}
            present = [k for k in r if k in allowed]
            if len(present) > 1:
                raise ValueError(f"{ref(r)}: multiple values for {field}")
        for field, targets in contract.get("references", {}).items():
            for target in references(r.get(field)):
                if "/" in target and "Resource" not in targets:
                    if target.split("/")[0] not in targets:
                        raise ValueError(f"{ref(r)}: invalid {field} target {target}")


def validate_episode(compiled):
    """Episode relationships that JSON schema and base FHIR do not require."""
    index = {ref(r): r for r in compiled["resources"]}
    labels = compiled["labels"]
    order, specimen, report = (
        index[labels[k]] for k in ("lab-order", "lab-specimen", "lab-report")
    )
    if reference(order) not in specimen.get("request", []):
        raise ValueError("Specimen belongs to another laboratory order")
    if report.get("basedOn") != [reference(order)] or report.get("specimen") != [
        reference(specimen)
    ]:
        raise ValueError("Report/order/specimen linkage mismatch")
    collected = specimen.get("collection", {}).get("collectedDateTime")
    if collected and (
        order["authoredOn"] > collected or specimen["receivedTime"] < collected
    ):
        raise ValueError("Invalid order/collection/receipt chronology")
    for target in report.get("result", []):
        result = index[target["reference"]]
        for r in [
            result,
            *[index[m["reference"]] for m in result.get("hasMember", [])],
        ]:
            if r.get("basedOn") != [reference(order)] or r.get("specimen") != reference(
                specimen
            ):
                raise ValueError("Result belongs to another order or specimen")
            if r["effectiveDateTime"] != collected or r["issued"] > report["issued"]:
                raise ValueError("Result/report collection chronology mismatch")
    if specimen["status"] != "available" and report.get("result"):
        raise ValueError("Unavailable/rejected specimen must not yield usable results")
    dispense, request = index[labels["dispense"]], index[labels["prescription"]]
    if dispense["authorizingPrescription"] != [reference(request)]:
        raise ValueError("Dispense linked to wrong prescription")
    if (
        dispense.get("whenHandedOver", dispense["whenPrepared"])
        < dispense["whenPrepared"]
    ):
        raise ValueError("Handover precedes preparation")
    appointment, slot = index[labels["appointment"]], index[labels["slot"]]
    if appointment["slot"] != [reference(slot)] or slot["status"] != "busy":
        raise ValueError("Appointment capacity is not reserved")


def quantity(value, unit):
    return {"value": value, "unit": unit, "system": UCUM, "code": unit}


def plan_episode(patient, snapshot_date, index, seed=20261006):
    """Generate an explicit event plan before any FHIR serialization.

    The cohort index balances branch/encoding coverage; RNG selects nuisance
    details. Clinical decisions are supplied facts, not decisions made by code.
    """
    birth, at = (
        date.fromisoformat(patient["birthDate"]),
        date.fromisoformat(snapshot_date),
    )
    age = at.year - birth.year - ((at.month, at.day) < (birth.month, birth.day))
    rng = random.Random(stable_id(seed, patient["id"]))
    assignment = index + index // 4
    branch = ("final", "pending", "rejected", "corrected")[assignment % 4]
    encoding = ("members", "components", "individual")[index // 4 % 3]
    return {
        "patient_id": patient["id"],
        "snapshot_date": snapshot_date,
        "age": age,
        "seed": seed,
        "index": index,
        "lab_branch": branch,
        "panel_encoding": encoding,
        "medication_reference": bool(assignment % 2),
        "dispense_handed_over": bool((index // 2 + index // 4) % 2),
        "eligibility_inforce": bool(index // 3 % 2),
        "eligibility_period_present": bool((index // 2 + index // 4) % 2),
        "lab_values": [round(rng.uniform(4.0, 5.5), 2), round(rng.uniform(0.7, 1.7), 2)]
        if age >= 18
        else [round(rng.uniform(11.5, 13.5), 1)],
        "bp": [rng.randrange(88, 107), rng.randrange(55, 69)]
        if age < 12
        else [rng.randrange(108, 128), rng.randrange(65, 81)],
        "events": [
            {"day": -12, "event": "visit_completed"},
            {"day": -12, "event": "laboratory_ordered"},
            {"day": -10, "event": "laboratory_" + branch},
            *(
                [{"day": -8, "event": "laboratory_correction_issued"}]
                if branch == "corrected"
                else []
            ),
            {"day": -7, "event": "medication_prescribed"},
            {"day": -5, "event": "medication_prepared"},
            *(
                [{"day": -4, "event": "medication_handed_over"}]
                if (index // 2 + index // 4) % 2
                else []
            ),
            {"day": -3, "event": "eligibility_response_received"},
            {"day": -2, "event": "followup_appointment_booked"},
            {"day": -1, "event": "patient_asks_for_followup"},
        ],
    }


class Compiler:
    def __init__(self, patient, plan):
        self.patient, self.plan = copy.deepcopy(patient), copy.deepcopy(plan)
        self.at = datetime.combine(
            date.fromisoformat(plan["snapshot_date"]), datetime.min.time(), timezone.utc
        )
        self.resources, self.by_label, self.history, self.events = [], {}, [], []
        self.standards = catalog()

    def time(self, day, hour=10):
        return (
            (self.at + timedelta(days=day, hours=hour))
            .isoformat()
            .replace("+00:00", "Z")
        )

    def add(self, label, kind, day=-30, **fields):
        identity = stable_id(
            PILOT_VERSION, self.plan["seed"], self.patient["id"], label
        )
        r = {
            "resourceType": kind,
            "id": identity,
            "meta": {
                "versionId": "1",
                "lastUpdated": self.time(day),
                "tag": [{"system": ORIGIN_SYSTEM, "code": "generated-event-pilot"}],
            },
            **copy.deepcopy(fields),
        }
        identifier_shape = schema()["definitions"][kind]["properties"].get("identifier")
        if identifier_shape:
            identifier = {"system": ID_SYSTEM, "value": identity[:12].upper()}
            r.setdefault(
                "identifier",
                [identifier] if identifier_shape.get("type") == "array" else identifier,
            )
        if label in self.by_label:
            raise ValueError("Duplicate event identity")
        self.by_label[label] = r
        self.resources.append(r)
        self.events.append(
            {"label": label, "event": "created", "at": self.time(day), "ref": ref(r)}
        )
        return r

    def update(self, label, day, **fields):
        r = self.by_label[label]
        self.history.append(copy.deepcopy(r))
        r.update(copy.deepcopy(fields))
        r["meta"]["versionId"] = str(int(r["meta"]["versionId"]) + 1)
        r["meta"]["lastUpdated"] = self.time(day)
        self.events.append(
            {
                "label": label,
                "event": "updated",
                "at": self.time(day),
                "ref": ref(r),
                "fields": copy.deepcopy(fields),
            }
        )
        return r

    @property
    def subject(self):
        return reference(self.patient)

    @property
    def reporter(self):
        return reference(self.by_label.get("caregiver", self.patient))

    def document(self, label, title, text, day):
        return self.add(
            label,
            "DocumentReference",
            day,
            status="current",
            subject=self.subject,
            type=cc(label, title),
            date=self.time(day),
            author=[reference(self.by_label["clinician-role"])],
            content=[
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "title": title,
                        "data": base64.b64encode(
                            ("SYNTHETIC PILOT. " + text).encode()
                        ).decode(),
                    }
                }
            ],
        )

    def infrastructure(self):
        if self.plan["age"] < 18:
            self.add(
                "caregiver",
                "RelatedPerson",
                active=True,
                patient=self.subject,
                relationship=[{"text": "Caregiver"}],
                name=[{"family": "Example", "given": ["Caregiver"]}],
            )
        org = self.add(
            "clinic", "Organization", active=True, name="Example Community Clinic"
        )
        lab = self.add(
            "laboratory",
            "Organization",
            active=True,
            name="Example Reference Laboratory",
        )
        payer = self.add(
            "payer", "Organization", active=True, name="Example Health Plan"
        )
        clinician = self.add(
            "clinician",
            "Practitioner",
            active=True,
            name=[{"family": "Example", "given": ["Morgan"]}],
        )
        role = self.add(
            "clinician-role",
            "PractitionerRole",
            active=True,
            practitioner=reference(clinician),
            organization=reference(org),
            code=[cc("coordinator", "Clinic coordinator")],
        )
        location = self.add(
            "clinic-location",
            "Location",
            status="active",
            name="Clinic room 2",
            managingOrganization=reference(org),
        )
        encounter = self.add(
            "visit",
            "Encounter",
            -12,
            status="finished",
            subject=self.subject,
            **{
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                }
            },
            period={"start": self.time(-12), "end": self.time(-12, 11)},
            serviceProvider=reference(org),
            participant=[{"individual": reference(role)}],
            location=[{"location": reference(location)}],
        )
        if self.plan["age"] >= 5:
            pattern = self.standards["blood_pressure"]
            # Keep standard LOINC examples, replacing the example's local namespaces.
            component_codes = [
                {"coding": [c for c in code["coding"] if c["system"] == LOINC]}
                for code in pattern["component_codes"]
            ]
            self.add(
                "blood-pressure",
                "Observation",
                -12,
                status="final",
                code=pattern["code"],
                category=[
                    cc(
                        "vital-signs",
                        system="http://terminology.hl7.org/CodeSystem/observation-category",
                    )
                ],
                subject=self.subject,
                encounter=reference(encounter),
                effectiveDateTime=self.time(-12),
                performer=[reference(role)],
                component=[
                    {"code": c, "valueQuantity": quantity(v, "mm[Hg]")}
                    for c, v in zip(component_codes, self.plan["bp"])
                ],
            )
        else:
            self.add(
                "temperature",
                "Observation",
                -12,
                status="final",
                code=cc("temperature", "Measured temperature"),
                subject=self.subject,
                encounter=reference(encounter),
                effectiveDateTime=self.time(-12),
                valueQuantity=quantity(36.8, "Cel"),
            )
        return org, lab, payer, role, encounter

    def diagnostics(self, lab, role, encounter):
        p = self.plan
        tests = (
            self.standards["lipid_tests"]
            if p["age"] >= 18
            else [{"code": cc("718-7", "Hemoglobin", LOINC), "unit_code": "g/dL"}]
        )
        panel_code = (
            cc("selected-lipids", "Selected lipid measurements")
            if p["age"] >= 18
            else cc("blood-count-subset", "Selected blood count result")
        )
        order = self.add(
            "lab-order",
            "ServiceRequest",
            -12,
            status="active",
            intent="order",
            subject=self.subject,
            code=panel_code,
            authoredOn=self.time(-12),
            requester=reference(role),
            performer=[reference(lab)],
            encounter=reference(encounter),
        )
        # A second identifier tests namespace-aware lookup without changing identity.
        order["identifier"].append(
            {"system": ID_SYSTEM + "/accession", "value": "A-" + order["id"][:8]}
        )
        collected = p["lab_branch"] != "pending"
        specimen = self.add(
            "lab-specimen",
            "Specimen",
            -10,
            status="unavailable"
            if not collected
            else "unsatisfactory"
            if p["lab_branch"] == "rejected"
            else "available",
            subject=self.subject,
            type={"text": "Serum" if p["age"] >= 18 else "Whole blood"},
            request=[reference(order)],
            **(
                {
                    "collection": {"collectedDateTime": self.time(-10, 8)},
                    "receivedTime": self.time(-10, 9),
                }
                if collected
                else {}
            ),
        )
        if p["lab_branch"] == "rejected":
            specimen["note"] = [
                {
                    "text": "Specimen rejected after a transport leak; no usable result. Replacement collection requested."
                }
            ]
        results = []
        valid = p["lab_branch"] in {"final", "corrected"}
        initial_values = (
            [v + 0.3 for v in p["lab_values"]]
            if p["lab_branch"] == "corrected"
            else p["lab_values"]
        )
        common = {
            "subject": self.subject,
            "basedOn": [reference(order)],
            "specimen": reference(specimen),
            "effectiveDateTime": self.time(-10, 8),
            "issued": self.time(-10, 12),
            "performer": [reference(lab)],
            "category": [
                cc(
                    "laboratory",
                    system="http://terminology.hl7.org/CodeSystem/observation-category",
                )
            ],
        }
        if valid and p["panel_encoding"] == "components":
            r = self.add(
                "lab-panel",
                "Observation",
                -10,
                status="final",
                code=panel_code,
                **common,
                component=[
                    {
                        "code": t["code"],
                        "valueQuantity": quantity(round(v, 2), t["unit_code"]),
                    }
                    for t, v in zip(tests, initial_values)
                ],
            )
            results = [r]
        elif valid:
            children = [
                self.add(
                    "lab-result-" + str(i),
                    "Observation",
                    -10,
                    status="final",
                    code=t["code"],
                    **common,
                    valueQuantity=quantity(round(v, 2), t["unit_code"]),
                )
                for i, (t, v) in enumerate(zip(tests, initial_values))
            ]
            results = children
            if p["panel_encoding"] == "members":
                panel = self.add(
                    "lab-panel",
                    "Observation",
                    -10,
                    status="final",
                    code=panel_code,
                    **common,
                    hasMember=[reference(r) for r in children],
                )
                results = [panel]
        self.add(
            "lab-report",
            "DiagnosticReport",
            -10,
            status="final" if valid else "registered" if not collected else "cancelled",
            code=panel_code,
            subject=self.subject,
            basedOn=[reference(order)],
            specimen=[reference(specimen)],
            performer=[reference(lab)],
            issued=self.time(-10, 12),
            **({"effectiveDateTime": self.time(-10, 8)} if collected else {}),
            **({"result": [reference(r) for r in results]} if results else {}),
        )
        if valid:
            self.update("lab-order", -10, status="completed")
        if p["lab_branch"] == "corrected":
            for label, r in list(self.by_label.items()):
                if r["resourceType"] != "Observation" or label == "blood-pressure":
                    continue
                if "component" in r:
                    components = copy.deepcopy(r["component"])
                    for component, v in zip(components, p["lab_values"]):
                        component["valueQuantity"]["value"] = v
                    self.update(
                        label,
                        -8,
                        status="corrected",
                        component=components,
                        issued=self.time(-8),
                    )
                elif label.startswith("lab-result-"):
                    i = int(label.rsplit("-", 1)[1])
                    self.update(
                        label,
                        -8,
                        status="corrected",
                        valueQuantity=quantity(
                            p["lab_values"][i], tests[i]["unit_code"]
                        ),
                        issued=self.time(-8),
                    )
                elif label == "lab-panel":
                    self.update(label, -8, status="corrected", issued=self.time(-8))
            self.update(
                "lab-report",
                -8,
                status="corrected",
                issued=self.time(-8),
                conclusion="Reissued after correcting a transcription error.",
            )
        # An older episode of the same test makes unbounded/latest-only searches insufficient.
        older = self.add(
            "older-lab-order",
            "ServiceRequest",
            -25,
            status="completed",
            intent="order",
            subject=self.subject,
            code=panel_code,
            authoredOn=self.time(-25),
        )
        old_obs = self.add(
            "older-lab-result",
            "Observation",
            -24,
            status="final",
            code=tests[0]["code"],
            subject=self.subject,
            basedOn=[reference(older)],
            effectiveDateTime=self.time(-24),
            valueQuantity=quantity(p["lab_values"][0], tests[0]["unit_code"]),
        )
        self.add(
            "older-lab-report",
            "DiagnosticReport",
            -24,
            status="final",
            code=panel_code,
            subject=self.subject,
            basedOn=[reference(older)],
            effectiveDateTime=self.time(-24),
            issued=self.time(-24, 12),
            result=[reference(old_obs)],
        )
        state = {
            "final": "results available",
            "corrected": "corrected results available",
            "pending": "awaiting collection",
            "rejected": "recollection needed",
        }[p["lab_branch"]]
        self.document(
            "lab-note",
            "Laboratory coordination",
            f"Laboratory follow-up for the order placed {self.time(-12)[:10]}: {state}. "
            + (
                "The earlier report belongs to a different order. No result is available for the current order."
                if not valid
                else "Report the issued measurements without interpreting them."
            ),
            -1,
        )

    def medication(self, role):
        adult = self.plan["age"] >= 18
        concept = cc(
            "acetaminophen-tablet" if adult else "acetaminophen-solution",
            "Acetaminophen 500 mg tablet"
            if adult
            else "Acetaminophen 160 mg/5 mL oral solution",
        )
        medication = self.add(
            "medication", "Medication", -7, status="active", code=concept
        )
        choice = (
            {"medicationReference": reference(medication)}
            if self.plan["medication_reference"]
            else {"medicationCodeableConcept": concept}
        )
        request = self.add(
            "prescription",
            "MedicationRequest",
            -7,
            status="active",
            intent="order",
            subject=self.subject,
            authoredOn=self.time(-7),
            requester=reference(role),
            **choice,
            dosageInstruction=[
                {
                    "text": "Dose instructions unavailable in the received prescription record."
                }
            ],
        )
        dispense = self.add(
            "dispense",
            "MedicationDispense",
            -5,
            status="preparation",
            subject=self.subject,
            **choice,
            authorizingPrescription=[reference(request)],
            whenPrepared=self.time(-5),
            quantity=quantity(20 if adult else 100, "{tbl}" if adult else "mL"),
        )
        if self.plan["dispense_handed_over"]:
            self.update(
                "dispense", -4, status="completed", whenHandedOver=self.time(-4)
            )
        self.add(
            "patient-medication-report",
            "MedicationStatement",
            -1,
            status="unknown",
            subject=self.subject,
            medicationReference=reference(medication),
            dateAsserted=self.time(-1),
            informationSource=self.reporter,
            derivedFrom=[reference(dispense)],
            note=[
                {
                    "text": "Caller could not confirm whether any dose was taken. Dispensing is not evidence of administration."
                }
            ],
        )
        self.document(
            "medication-note",
            "Pharmacy handoff",
            "Pharmacy preparation is documented. "
            + (
                "Handover is documented; the caller cannot confirm use."
                if self.plan["dispense_handed_over"]
                else "Pickup has not been documented; the caller cannot confirm use."
            ),
            -1,
        )

    def scheduling(self, role):
        schedule = self.add(
            "schedule",
            "Schedule",
            -2,
            active=True,
            actor=[reference(role)],
            planningHorizon={"start": self.time(5, 0), "end": self.time(6, 0)},
        )
        slot = self.add(
            "slot",
            "Slot",
            -2,
            schedule=reference(schedule),
            status="free",
            start=self.time(5),
            end=self.time(5)[:11] + "10:30:00Z",
        )
        appointment = self.add(
            "appointment",
            "Appointment",
            -2,
            status="proposed",
            description="Laboratory follow-up",
            start=slot["start"],
            end=slot["end"],
            slot=[reference(slot)],
            participant=[
                {"actor": self.subject, "status": "needs-action"},
                {"actor": reference(role), "status": "accepted"},
            ],
        )
        self.update("slot", -2, status="busy")
        participants = copy.deepcopy(appointment["participant"])
        participants[0]["status"] = "accepted"
        self.update("appointment", -2, status="booked", participant=participants)

    def insurance(self, payer, org):
        coverage = self.add(
            "coverage",
            "Coverage",
            -20,
            status="active",
            beneficiary=self.subject,
            payor=[reference(payer)],
            period={"start": self.time(-180)[:10], "end": self.time(180)[:10]},
            **{
                "class": [
                    {
                        "type": cc(
                            "plan",
                            system="http://terminology.hl7.org/CodeSystem/coverage-class",
                        ),
                        "value": "EXAMPLE-PPO",
                    }
                ]
            },
        )
        request = self.add(
            "eligibility-request",
            "CoverageEligibilityRequest",
            -4,
            status="active",
            purpose=["validation"],
            patient=self.subject,
            created=self.time(-4),
            provider=reference(org),
            insurer=reference(payer),
            servicedDate=self.time(5)[:10],
            insurance=[{"coverage": reference(coverage), "focal": True}],
        )
        insurance = {
            "coverage": reference(coverage),
            "inforce": self.plan["eligibility_inforce"],
        }
        if self.plan["eligibility_period_present"]:
            insurance["benefitPeriod"] = {
                "start": self.time(-30)[:10],
                "end": self.time(30)[:10],
            }
        self.add(
            "eligibility-response",
            "CoverageEligibilityResponse",
            -3,
            status="active",
            purpose=["validation"],
            patient=self.subject,
            created=self.time(-3),
            request=reference(request),
            outcome="complete",
            insurer=reference(payer),
            servicedDate=self.time(5)[:10],
            insurance=[insurance],
            disposition="Eligibility response for the requested service date; no payment or prior authorization guarantee.",
        )

    def followup(self, role):
        msg = self.add(
            "call",
            "Communication",
            -1,
            status="completed",
            subject=self.subject,
            sender=self.reporter,
            recipient=[reference(role)],
            sent=self.time(-1),
            received=self.time(-1),
            about=[reference(self.by_label["lab-order"])],
            basedOn=[reference(self.by_label["lab-order"])],
            payload=[
                {
                    "contentString": "Please check the status of my recent laboratory order and send a follow-up request to the clinic. I also need to cancel the booked follow-up visit."
                }
            ],
        )
        self.add(
            "reported-medication-use",
            "Observation",
            -1,
            status="final",
            subject=self.subject,
            code=cc(
                "medication-use-confirmation", "Caller confirmation of medication use"
            ),
            effectiveDateTime=self.time(-1),
            performer=[self.reporter],
            basedOn=[reference(self.by_label["prescription"])],
            valueCodeableConcept=cc(
                "unable-to-confirm", "Caller unable to confirm use"
            ),
        )
        self.add(
            "reported-weight-unavailable",
            "Observation",
            -1,
            status="final",
            subject=self.subject,
            code=cc("reported-weight", "Patient-reported current weight"),
            effectiveDateTime=self.time(-1),
            performer=[self.reporter],
            dataAbsentReason=cc(
                "asked-unknown",
                system="http://terminology.hl7.org/CodeSystem/data-absent-reason",
            ),
            note=[
                {
                    "text": "During the follow-up call, the caller did not know a current weight. Earlier measured values remain separate."
                }
            ],
        )
        # This removable duplicate is deliberately unreferenced. Clinical history stays intact.
        self.add(
            "original-triage",
            "Task",
            -1,
            status="requested",
            intent="order",
            **{"for": self.subject},
            focus=reference(msg),
            code=cc("telephone-triage", "Telephone request triage"),
            description="Review the caller's request.",
        )
        self.add(
            "duplicate-draft",
            "Task",
            -1,
            status="draft",
            intent="order",
            **{"for": self.subject},
            focus=reference(msg),
            code=cc("telephone-triage", "Telephone request triage"),
            description="Unsubmitted duplicate of the caller follow-up request, created in error.",
        )
        questionnaire = self.add(
            "intake-form",
            "Questionnaire",
            -30,
            url=ID_SYSTEM + "/intake/" + self.patient["id"],
            status="active",
            item=[
                {
                    "linkId": "contact",
                    "text": "Preferred contact method",
                    "type": "choice",
                    "answerOption": [
                        {"valueString": "phone"},
                        {"valueString": "portal"},
                    ],
                }
            ],
        )
        self.add(
            "intake-response",
            "QuestionnaireResponse",
            -30,
            status="completed",
            subject=self.subject,
            questionnaire=questionnaire["url"],
            authored=self.time(-30),
            source=self.reporter,
            item=[
                {
                    "linkId": "contact",
                    "answer": [
                        {"valueString": "phone" if self.plan["index"] % 2 else "portal"}
                    ],
                }
            ],
        )

    def compile(self):
        org, lab, payer, role, encounter = self.infrastructure()
        self.diagnostics(lab, role, encounter)
        self.medication(role)
        self.scheduling(role)
        self.insurance(payer, org)
        self.followup(role)
        validate_graph([self.patient, *self.resources])
        validate_contracts(self.resources)
        compiled = {
            "resources": self.resources,
            "plan": self.plan,
            "history": self.history,
            "events": self.events,
            "labels": {k: ref(v) for k, v in self.by_label.items()},
        }
        validate_episode(compiled)
        return compiled


def lab_answer(index, labels):
    """Oracle is derived from the serialized chart, not a copied planned answer."""
    report, specimen = index[labels["lab-report"]], index[labels["lab-specimen"]]
    measurements = []

    def visit(r):
        if r.get("valueQuantity"):
            measurements.append(
                {
                    "code": r["code"]["coding"][0]["code"],
                    "value": r["valueQuantity"]["value"],
                    "unit": r["valueQuantity"]["code"],
                }
            )
        for c in r.get("component", []):
            if c.get("valueQuantity"):
                measurements.append(
                    {
                        "code": c["code"]["coding"][0]["code"],
                        "value": c["valueQuantity"]["value"],
                        "unit": c["valueQuantity"]["code"],
                    }
                )
        for child in r.get("hasMember", []):
            visit(index[child["reference"]])

    for result in report.get("result", []):
        visit(index[result["reference"]])
    return {
        "report_status": report["status"],
        "specimen_status": specimen["status"],
        "measurements": sorted(measurements, key=lambda x: x["code"]),
    }


def make_tasks(patient, compiled, shard):
    index = {ref(r): r for r in compiled["resources"]}
    labels, plan = compiled["labels"], compiled["plan"]
    index[ref(patient)] = patient
    mrn = next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)
    patient_step = {"method": "GET", "path": f"Patient?identifier={MRN_SYSTEM}|{mrn}"}
    tasks = []

    def emit(
        family,
        instruction,
        answer,
        evidence,
        steps,
        *,
        updates=(),
        creates=(),
        deletes=(),
        atomic=False,
        writable_refs=(),
    ):
        evidence = [ref(patient), *evidence]
        changes = [
            {"ref": key, "field": field, "value": value}
            for key, fields in updates
            for field, value in fields.items()
        ]
        writes = [
            {
                "method": "PUT",
                "path": key,
                "body": {**copy.deepcopy(index[key]), **fields},
                "headers": {"If-Match": 'W/"' + index[key]["meta"]["versionId"] + '"'},
            }
            for key, fields in updates
        ]
        writes += [
            {"method": "POST", "path": r["resourceType"], "body": r} for r in creates
        ]
        writes += [
            {"method": "DELETE", "path": key, "headers": {"If-Match": 'W/"1"'}}
            for key in deletes
        ]
        if atomic:
            writes = [
                {
                    "method": "POST",
                    "path": "",
                    "body": {
                        "resourceType": "Bundle",
                        "type": "transaction",
                        "entry": [
                            {
                                "request": {
                                    "method": w["method"],
                                    "url": w["path"],
                                    "ifMatch": w["headers"]["If-Match"],
                                },
                                "resource": w["body"],
                            }
                            for w in writes
                        ],
                    },
                }
            ]
        all_steps = [patient_step, *steps, *writes]
        tasks.append(
            {
                "id": stable_id(PILOT_VERSION, patient["id"], family),
                "family": family,
                "split": "dev",
                "upstream_split": "train",
                "patient_id": patient["id"],
                "snapshot_date": plan["snapshot_date"],
                "shard": shard,
                "prompt": f"For the patient with MRN {mrn}, {instruction}\nReturn JSON with answer and evidence (retrieved FHIR Type/id references).",
                "omit": [],
                "writable_types": sorted(
                    {index[k]["resourceType"] for k, _ in updates}
                    | {r["resourceType"] for r in creates}
                    | {index[k]["resourceType"] for k in deletes}
                ),
                "writable_refs": list(writable_refs),
                "delete_refs": list(deletes),
                "requires_read_before_write": bool(writes),
                "requires_transaction": atomic,
                "dependency_depth": 4 if family.startswith("pilot_lab") else 3,
                "depth_basis": "Declared reference graph; not minimum API calls",
                "reference_calls": len(all_steps),
                "reference_steps": all_steps,
                "gold": {
                    "answer": answer,
                    "evidence": evidence,
                    "evidence_fields": {
                        k: sorted(set(index[k]) - {"id", "meta", "resourceType"})
                        for k in evidence
                    },
                    "creates": list(creates),
                    "updates": changes,
                    "deletes": list(deletes),
                },
            }
        )

    def search(label, extra=""):
        r = index[labels[label]]
        i = r["identifier"][0]
        return {
            "method": "GET",
            "path": f"{r['resourceType']}?identifier={i['system']}|{i['value']}"
            + extra,
        }

    report = index[labels["lab-report"]]
    result_refs = [r["reference"] for r in report.get("result", [])]
    member_refs = [
        r["reference"] for key in result_refs for r in index[key].get("hasMember", [])
    ]
    lab_evidence = (
        [labels[k] for k in ("lab-order", "lab-specimen", "lab-report")]
        + result_refs
        + member_refs
    )
    accession = index[labels["lab-order"]]["identifier"][1]["value"]
    lab_steps = [
        {
            "method": "GET",
            "path": f"ServiceRequest?identifier={ID_SYSTEM}/accession|{accession}",
        },
        {
            "method": "GET",
            "path": f"DiagnosticReport?patient={patient['id']}&based-on={labels['lab-order']}&_include=DiagnosticReport:result",
        },
        {"method": "GET", "path": labels["lab-specimen"]},
        *[{"method": "GET", "path": k} for k in member_refs],
    ]
    answer = lab_answer(index, labels)
    instruction = f"report the current result for laboratory accession {accession}; distinguish it from older orders. Return report_status, specimen_status and measurements (code, value, unit), sorted by code."
    emit("pilot_lab_result", instruction, answer, lab_evidence, lab_steps)
    create = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": reference(patient),
        "focus": {"reference": labels["lab-order"]},
        "authoredOn": plan["snapshot_date"],
        "description": "Review laboratory follow-up requested by caller.",
    }
    emit(
        "pilot_lab_followup",
        instruction
        + " Also create the requested clinic follow-up Task with status=requested, intent=order, for=this patient, focus=the order, "
        + f"authoredOn={plan['snapshot_date']}, description='Review laboratory follow-up requested by caller.'. Return the same answer fields.",
        answer,
        [*lab_evidence, labels["call"]],
        [
            *lab_steps,
            {
                "method": "GET",
                "path": f"Communication?patient={patient['id']}&based-on={labels['lab-order']}&_count=50",
            },
        ],
        creates=[create],
    )
    rx, dispense, statement = (
        index[labels[k]]
        for k in ("prescription", "dispense", "patient-medication-report")
    )
    medication_code = (
        "acetaminophen-tablet" if plan["age"] >= 18 else "acetaminophen-solution"
    )
    med_query = (
        f"patient={patient['id']}&code={LOCAL_SYSTEM}|{medication_code}&_count=50"
    )
    emit(
        "pilot_medication_handoff",
        f"check the pharmacy handoff for local medication code {medication_code}, prescribed on {rx['authoredOn'][:10]}. Return dispense_status, handed_over (boolean), and use_status from the patient report; do not infer administration.",
        {
            "dispense_status": dispense["status"],
            "handed_over": "whenHandedOver" in dispense,
            "use_status": statement["status"],
        },
        [ref(rx), ref(dispense), ref(statement)],
        [
            {"method": "GET", "path": "MedicationRequest?" + med_query},
            {"method": "GET", "path": "MedicationDispense?" + med_query},
            {"method": "GET", "path": "MedicationStatement?" + med_query},
        ],
    )
    appt, slot = index[labels["appointment"]], index[labels["slot"]]
    emit(
        "pilot_cancel_appointment",
        f"cancel the booked laboratory follow-up appointment starting {appt['start']} and release its slot atomically. Set Appointment.status=cancelled and Slot.status=free, preserving all other fields. Return cancelled=true and slot_released=true.",
        {"cancelled": True, "slot_released": True},
        [ref(appt), ref(slot)],
        [
            {
                "method": "GET",
                "path": f"Appointment?patient={patient['id']}&status=booked&date=eq{appt['start'][:10]}&_include=Appointment:slot&_count=50",
            }
        ],
        updates=[(ref(appt), {"status": "cancelled"}), (ref(slot), {"status": "free"})],
        atomic=True,
        writable_refs=[ref(slot)],
    )
    coverage, response, request = (
        index[labels[k]]
        for k in ("coverage", "eligibility-response", "eligibility-request")
    )
    emit(
        "pilot_eligibility",
        f"check the insurer response for service date {request['servicedDate']}. Return coverage_status, serviced_date, inforce, and benefit_period_documented; use the insurer response rather than registration status.",
        {
            "coverage_status": coverage["status"],
            "serviced_date": response["servicedDate"],
            "inforce": response["insurance"][0]["inforce"],
            "benefit_period_documented": "benefitPeriod" in response["insurance"][0],
        },
        [ref(coverage), ref(response), ref(request)],
        [
            {
                "method": "GET",
                "path": f"CoverageEligibilityResponse?patient={patient['id']}&_include=CoverageEligibilityResponse:request&_count=50",
            },
            {"method": "GET", "path": ref(coverage)},
        ],
    )
    draft = index[labels["duplicate-draft"]]
    draft_id = draft["identifier"][0]["value"]
    emit(
        "pilot_delete_duplicate",
        f"remove only unsubmitted duplicate Task {draft_id}, after confirming its draft status, linked call and existing requested triage Task for that call. Preserve the original triage Task. This deletion is explicitly authorized. Return removed=true and original_retained=true.",
        {"removed": True, "original_retained": True},
        [ref(draft), labels["call"], labels["original-triage"]],
        [
            search("duplicate-draft", "&_include=Task:focus"),
            {
                "method": "GET",
                "path": f"Task?patient={patient['id']}&focus={labels['call']}&_count=50",
            },
        ],
        deletes=[ref(draft)],
    )
    return tasks


def compile_episode(patient, plan):
    return Compiler(patient, plan).compile()
