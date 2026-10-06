"""Authored, linked synthetic workflow episodes; no latent source facts are inferred.

Each patient receives a seeded subset of modules. Clinical decisions are supplied
by the vignette; the agent retrieves evidence and executes bounded chart work.
"""

import base64
import copy
import json
from datetime import timedelta

from .dataset import LOCAL_SYSTEM, LOINC, MRN_SYSTEM, cc, reference, stable_id
from .validation import ref

MODULES = (
    "diagnostics",
    "referral",
    "medication",
    "immunization",
    "revenue",
    "equipment",
    "records",
    "nursing",
)


class Episode:
    def __init__(self, source, handles, snapshot, rng, add):
        self.source, self.h, self.at, self.rng, self.add = (
            source,
            handles,
            snapshot,
            rng,
            add,
        )
        self.p = handles["patient"]
        self.patient = reference(self.p)
        self.tasks, self.deletable, self.modules = [], [], []
        self.module = "registration"
        self.variant = "routine"
        self.origin = "generated-workflow-v3"

    def day(self, offset=0, hour="10:00:00"):
        return (self.at + timedelta(days=offset)).isoformat() + "T" + hour + "Z"

    def resource(self, label, kind, **fields):
        # Business IDs are visible search keys, distinct from opaque FHIR resource IDs.
        fields.setdefault(
            "identifier",
            [
                {
                    "system": LOCAL_SYSTEM + "/workflow-id",
                    "value": "WF-" + stable_id(self.source["source_id"], label)[:12],
                }
            ],
        )
        return self.add("v3-" + label, kind, self.origin, **fields)

    def document(self, label, title, text, offset=-1):
        return self.resource(
            label,
            "DocumentReference",
            status="current",
            subject=self.patient,
            type=cc(label, title),
            date=self.day(offset),
            author=[reference(self.h["practitioner"])],
            content=[
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "title": title,
                        "data": base64.b64encode(
                            ("SYNTHETIC WORKFLOW EXTENSION. " + text).encode()
                        ).decode(),
                    }
                }
            ],
        )

    def task_resource(self, label, focus, status="requested", **fields):
        return self.resource(
            label,
            "Task",
            status=status,
            intent="order",
            **{"for": self.patient},
            focus=reference(focus),
            authoredOn=self.day(-1),
            **fields,
        )

    def ident(self, resource):
        if not resource.get("identifier"):
            resource["identifier"] = [
                {
                    "system": LOCAL_SYSTEM + "/workflow-id",
                    "value": "WF-" + resource["id"][:12],
                }
            ]
        return resource["identifier"][0]["value"]

    def emit(
        self,
        family,
        role,
        story,
        answer,
        evidence,
        *,
        updates=(),
        creates=(),
        deletes=(),
        atomic=False,
        chain=(),
        searches=None,
        variant=None,
        conditional=False,
        write_contract=None,
    ):
        """Compile a task contract and an executable, non-minimal reference workflow.

        Symbolic names in create contracts are business identifiers. They are
        resolved by the agent, never hidden IDs handed out as gold evidence.
        """
        p = self.p
        evidence = [p, *evidence]
        by_ref = {ref(r): r for r in evidence}
        aliases = {
            key: ("patient" if key == ref(p) else self.ident(r))
            for key, r in by_ref.items()
        }

        def symbolic(x):
            if isinstance(x, dict):
                return {k: symbolic(v) for k, v in x.items()}
            if isinstance(x, list):
                return [symbolic(v) for v in x]
            return "$" + aliases[x] if isinstance(x, str) and x in aliases else x

        changes = [
            {"ref": ref(r), "field": field, "value": value}
            for r, fields in updates
            for field, value in fields.items()
        ]
        read_steps = [
            {
                "method": "GET",
                "path": f"Patient?identifier={MRN_SYSTEM}|{p['identifier'][0]['value']}",
            }
        ]
        # These searches are deliberately valid discovery plans, not optimal-hop labels.
        for r in evidence[1:]:
            read_steps.append(
                {
                    "method": "GET",
                    "path": (searches or {}).get(
                        ref(r), f"{r['resourceType']}?identifier={self.ident(r)}"
                    ),
                }
            )
        writes = []
        for r, fields in updates:
            writes.append(
                {
                    "method": "PUT",
                    "path": ref(r),
                    "body": {**copy.deepcopy(r), **fields},
                    "headers": {"If-Match": 'W/"1"'},
                }
            )
        for resource in creates:
            step = {
                "method": "POST",
                "path": resource["resourceType"],
                "body": resource,
            }
            if conditional:
                step["headers"] = {
                    "If-None-Exist": "identifier=" + resource["identifier"][0]["value"]
                }
            writes.append(step)
        for r in deletes:
            writes.append(
                {"method": "DELETE", "path": ref(r), "headers": {"If-Match": 'W/"1"'}}
            )
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
                                    "method": s["method"],
                                    "url": s["path"],
                                    **(
                                        {"ifMatch": s["headers"]["If-Match"]}
                                        if s.get("headers", {}).get("If-Match")
                                        else {}
                                    ),
                                },
                                **({"resource": s["body"]} if "body" in s else {}),
                            }
                            for s in writes
                        ],
                    },
                }
            ]
        context = ", ".join(
            f"{r['resourceType']} business identifier {self.ident(r)}"
            for r in evidence[1:]
        )
        contract = []
        if updates:
            contract.append(
                "Preserve all other fields. Apply these field changes after reading the current versions: "
                + json.dumps(
                    [
                        {"identifier": self.ident(r), "fields": symbolic(fields)}
                        for r, fields in updates
                    ]
                )
            )
        if creates:
            contract.append(
                "Create these structured records; $patient and $business-identifier placeholders must be replaced with retrieved Type/id references: "
                + json.dumps(symbolic(list(creates)))
            )
        if deletes:
            contract.append(
                "DELETE only the explicitly authorized unsubmitted duplicate draft, using its current ETag."
            )
        if write_contract is not None:
            contract = [write_contract]
        if atomic:
            contract.append(
                "Commit all changes together in one atomic FHIR transaction."
            )
        family_id = stable_id(self.source["source_id"], family)
        # All listed business identifiers can be searched directly. Relationship
        # depth is distinct from information dependency depth; never inflate hops.
        edges = [
            ("supplied_mrn", "patient"),
            ("supplied_case_identifiers", "episode_evidence"),
        ]
        task = {
            "id": family_id,
            "patient_id": p["id"],
            "split": self.source["split"],
            "upstream_split": self.source["upstream_split"],
            "family": family,
            "domain": self.module,
            "role": role,
            "scenario_variant": variant or self.variant,
            "age_band": "pediatric" if self.source["age"] < 18 else "adult",
            "snapshot_date": self.at.isoformat(),
            "prompt": f"You are assisting the {role}. As of {self.at.isoformat()}, the verified caller supplies MRN {p['identifier'][0]['value']} (system {MRN_SYSTEM}). {story}\nCase paperwork lists: {context}.\n"
            + "\n".join(contract)
            + "\nReturn JSON with exactly answer and evidence. Answer fields and types follow this contract: "
            + json.dumps({k: type(v).__name__ for k, v in answer.items()})
            + ". Evidence must list all supporting case records and the resolved Patient, using retrieved Type/id references. Do not infer undocumented care or send anything outside this sandbox.",
            "gold": {
                "answer": answer,
                "evidence": sorted(by_ref),
                "target_evidence": sorted(set(by_ref) - {ref(p)}),
                "evidence_fields": {
                    key: sorted(set(r) - {"id", "meta", "resourceType"})
                    for key, r in by_ref.items()
                },
                "updates": changes,
                "creates": list(creates),
                "deletes": [ref(r) for r in deletes],
            },
            "writable_types": sorted(
                {c["ref"].split("/")[0] for c in changes}
                | {r["resourceType"] for r in creates}
                | {r["resourceType"] for r in deletes}
            ),
            "writable_refs": [
                ref(r) for r, _ in updates if r["resourceType"] == "Slot"
            ],
            "delete_refs": [ref(r) for r in deletes],
            "omit": [],
            "resource_relationship_edges": list(chain),
            "dependency_edges": edges,
            "reference_steps": read_steps + writes,
            "reference_calls": len(read_steps + writes),
            "requires_transaction": atomic,
            "requires_read_before_write": True,
            "crud": "".join(
                x
                for x, yes in [
                    ("C", bool(creates)),
                    ("R", True),
                    ("U", bool(updates)),
                    ("D", bool(deletes)),
                ]
                if yes
            ),
        }
        self.tasks.append(task)
        return task

    def review(self, label, focus, description):
        return {
            "resourceType": "Task",
            "status": "requested",
            "intent": "order",
            "for": self.patient,
            "focus": reference(focus),
            "identifier": [
                {
                    "system": LOCAL_SYSTEM + "/workflow-id",
                    "value": "REVIEW-" + self.ident(focus),
                }
            ],
            "description": description,
        }

    def review_contract(self, focus, rule):
        body = self.review("review", focus, "$derive_from_chart")
        body["for"] = {"reference": "$patient"}
        body["focus"] = {"reference": "$" + self.ident(focus)}
        return (
            "Create exactly this Task, resolving reference placeholders and deriving the description from the chart: "
            + json.dumps(body)
            + ". "
            + rule
        )


def registration(b):
    b.module = "registration"
    phone = f"202-555-{100 + b.rng.randrange(100):04d}"
    report = b.resource(
        "contact-call",
        "Communication",
        status="completed",
        subject=b.patient,
        sent=b.day(),
        sender=reference(b.h["caregiver"]) if b.source["age"] < 18 else b.patient,
        payload=[
            {
                "contentString": f"Identity verified with MRN and DOB. Replace the home phone with {phone}; keep any email and mobile contacts. Address unchanged."
            }
        ],
    )
    b.p["telecom"] = [
        {"system": "phone", "value": "202-555-0199", "use": "home"},
        {"system": "email", "value": b.p["id"] + "@example.invalid"},
    ]
    telecom = [
        {**v, "value": phone}
        if v.get("system") == "phone" and v.get("use") == "home"
        else v
        for v in b.p["telecom"]
    ]
    b.emit(
        "verified_contact_change",
        "registration clerk",
        "The caller reports a new home telephone number in the verified telephone encounter. Update that number without changing other contacts. Return home_phone and other_contacts_preserved=true.",
        {"home_phone": phone, "other_contacts_preserved": True},
        [report],
        updates=[(b.p, {"telecom": telecom})],
    )
    flag = b.resource(
        "interpreter-flag",
        "Flag",
        status="active",
        subject=b.patient,
        code=cc("interpreter", "Interpreter required for clinical discussions"),
        period={"start": b.day(-30)},
        author=reference(b.h["practitioner"]),
    )
    b.emit(
        "interpreter_handoff",
        "front-desk coordinator",
        "Before the appointment, check the active communication-support flag. Arrange an interpreter follow-up task; do not mark the need resolved merely because a request was placed. Return interpreter_requested=true and flag_still_active=true.",
        {"interpreter_requested": True, "flag_still_active": True},
        [flag],
        creates=[
            b.review(
                "interpreter", flag, "Arrange interpreter for clinical discussion."
            )
        ],
    )


def scheduling(b):
    b.module = "scheduling"
    schedule = b.resource(
        "clinic-schedule",
        "Schedule",
        active=True,
        actor=[reference(b.h["practitioner"]), reference(b.h["location"])],
        planningHorizon={"start": b.day(1), "end": b.day(30)},
    )
    slots = [
        b.resource(
            "slot-" + str(i),
            "Slot",
            schedule=reference(schedule),
            status=status,
            start=b.day(day),
            end=b.day(day, "10:30:00"),
        )
        for i, (day, status) in enumerate(
            [(3, "busy"), (6, "free"), (8, "busy-unavailable")]
        )
    ]
    appt = b.resource(
        "followup-booking",
        "Appointment",
        status="booked",
        description="Follow-up; transport requires a later date",
        start=slots[0]["start"],
        end=slots[0]["end"],
        slot=[reference(slots[0])],
        participant=[
            {"actor": b.patient, "status": "accepted"},
            {"actor": reference(b.h["practitioner"]), "status": "accepted"},
        ],
    )
    b.emit(
        "reschedule_with_slot_release",
        "scheduling coordinator",
        "The patient cannot travel on the original date. Move the follow-up to the listed free slot with the same clinic. Release the old slot and reserve the new one; leave unavailable slots untouched. Return appointment_status, start, old_slot_status and new_slot_status.",
        {
            "appointment_status": "booked",
            "start": slots[1]["start"],
            "old_slot_status": "free",
            "new_slot_status": "busy",
        },
        [appt, schedule, slots[0], slots[1]],
        updates=[
            (
                appt,
                {
                    "start": slots[1]["start"],
                    "end": slots[1]["end"],
                    "slot": [reference(slots[1])],
                },
            ),
            (slots[0], {"status": "free"}),
            (slots[1], {"status": "busy"}),
        ],
        atomic=True,
        chain=[
            ("patient", "appointment"),
            ("appointment", "schedule"),
            ("schedule", "available_slot"),
        ],
    )
    b.emit(
        "cancel_visit_release_capacity",
        "scheduling coordinator",
        "The patient cancels this follow-up and declines rebooking today. Cancel the appointment and release its occupied slot together. Keep the appointment history. Return cancelled=true and slot_released=true.",
        {"cancelled": True, "slot_released": True},
        [appt, slots[0]],
        updates=[(appt, {"status": "cancelled"}), (slots[0], {"status": "free"})],
        atomic=True,
    )


def diagnostics(b):
    b.module = "diagnostics"
    # Pediatric charts use thyroid evaluation, not invented adult disease histories.
    choices = [
        (
            "3016-3",
            "TSH",
            "m[IU]/L",
            2.1,
            "pediatric follow-up laboratory evaluation"
            if b.source["age"] < 5
            else "evaluation of fatigue",
            "serum",
        )
    ]
    if b.source["age"] >= 18:
        choices += [
            (
                "4548-4",
                "HbA1c",
                "%",
                6.3,
                "glycemic monitoring requested by the clinician",
                "whole blood",
            ),
            (
                "2160-0",
                "Creatinine",
                "mg/dL",
                1.1,
                "renal laboratory follow-up",
                "serum",
            ),
        ]
    code, name, unit, baseline, reason, sample = b.rng.choice(choices)
    b.variant = name + "-" + b.rng.choice(["unlabelled", "insufficient_quantity"])
    order = b.resource(
        "repeat-lab-order",
        "ServiceRequest",
        status="active",
        intent="order",
        subject=b.patient,
        code=cc(code, name, LOINC),
        authoredOn=b.day(-10),
        requester=reference(b.h["practitioner"]),
        reasonCode=[{"text": reason}],
    )
    rejected = b.resource(
        "rejected-sample",
        "Specimen",
        status="unsatisfactory",
        subject=b.patient,
        type={"text": sample},
        request=[reference(order)],
        collection={"collectedDateTime": b.day(-3)},
        receivedTime=b.day(-3, "12:00:00"),
        note=[
            {
                "text": "Rejected: "
                + b.variant.split("-", 1)[1]
                + ". No valid result was produced for this collection."
            }
        ],
    )
    msg = b.resource(
        "recollection-message",
        "Communication",
        status="completed",
        subject=b.patient,
        basedOn=[reference(order)],
        sent=b.day(-2),
        payload=[
            {
                "contentString": "Laboratory requests a replacement specimen under the existing active order; call patient to arrange collection."
            }
        ],
        about=[reference(rejected)],
    )
    b.emit(
        "specimen_recollection_request",
        "laboratory coordinator",
        "The patient asks why the ordered test has no result. Read the laboratory rejection and arrange recollection under the existing order. Do not invent a result or close the order. Return specimen_status, result_available=false and recollection_requested=true.",
        {
            "specimen_status": "unsatisfactory",
            "result_available": False,
            "recollection_requested": True,
        },
        [order, rejected, msg],
        creates=[
            b.review(
                "recollect",
                order,
                "Arrange replacement specimen after laboratory rejection.",
            )
        ],
        chain=[
            ("patient", "order"),
            ("order", "specimen"),
            ("specimen", "lab_message"),
        ],
    )
    # A separate, completed order prevents confusing an old result with the rejected draw.
    old_order = b.resource(
        "completed-lab-order",
        "ServiceRequest",
        status="completed",
        intent="order",
        subject=b.patient,
        code=cc(code, name, LOINC),
        authoredOn=b.day(-25),
    )
    specimen = b.resource(
        "accepted-sample",
        "Specimen",
        status="available",
        subject=b.patient,
        type={"text": sample},
        request=[reference(old_order)],
        collection={"collectedDateTime": b.day(-20)},
    )
    value = round(baseline + b.rng.uniform(-0.2, 0.3), 2)
    result = b.resource(
        "corrected-lab-value",
        "Observation",
        status="corrected",
        subject=b.patient,
        code=cc(code, name, LOINC),
        effectiveDateTime=b.day(-20),
        issued=b.day(-18),
        basedOn=[reference(old_order)],
        specimen=reference(specimen),
        valueQuantity={
            "value": value,
            "unit": unit,
            "system": "http://unitsofmeasure.org",
            "code": unit,
        },
        note=[
            {
                "text": "Laboratory corrected a transcription error; this value supersedes the earlier report."
            }
        ],
    )
    report = b.resource(
        "corrected-lab-report",
        "DiagnosticReport",
        status="corrected",
        subject=b.patient,
        code=cc(code, name, LOINC),
        basedOn=[reference(old_order)],
        specimen=[reference(specimen)],
        result=[reference(result)],
        effectiveDateTime=b.day(-20),
        issued=b.day(-18),
    )
    b.emit(
        "corrected_result_provenance",
        "clinic nurse",
        "Report the corrected result from the completed order, with its collection time, and distinguish it from the more recent rejected collection. Return test, value, unit, collected_at and newer_sample_rejected.",
        {
            "test": name,
            "value": value,
            "unit": unit,
            "collected_at": specimen["collection"]["collectedDateTime"],
            "newer_sample_rejected": True,
        },
        [old_order, report, result, specimen, rejected],
        chain=[
            ("patient", "completed_order"),
            ("completed_order", "report"),
            ("report", "result"),
            ("result", "collection"),
        ],
    )


def referral(b):
    b.module = "referral"
    site = b.rng.choice(["left ankle", "right wrist"])
    order = b.resource(
        "imaging-order",
        "ServiceRequest",
        status="completed",
        intent="order",
        subject=b.patient,
        code=cc("radiograph", "Radiograph of " + site),
        authoredOn=b.day(-12),
    )
    study = b.resource(
        "imaging-study",
        "ImagingStudy",
        status="available",
        subject=b.patient,
        basedOn=[reference(order)],
        started=b.day(-9),
        modality=[
            {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "DX"}
        ],
        description="Synthetic radiographs of " + site,
        numberOfSeries=1,
        numberOfInstances=2,
    )
    report = b.resource(
        "imaging-report",
        "DiagnosticReport",
        status="final",
        subject=b.patient,
        code=cc("radiograph", "Radiograph of " + site),
        basedOn=[reference(order)],
        imagingStudy=[reference(study)],
        effectiveDateTime=b.day(-9),
        issued=b.day(-8),
        conclusion="No acute displaced fracture identified. Clinical follow-up recommended if symptoms persist.",
    )
    acknowledged = b.rng.choice([True, False])
    b.variant = "acknowledged" if acknowledged else "delivery_not_acknowledged"
    note = b.document(
        "specialist-reply",
        "Referral reply",
        f"Referral for persistent {site} pain after injury. Imaging available. "
        + (
            "Referring clinician has acknowledged the report and arranged follow-up."
            if acknowledged
            else "The report was delivered to the referring office. Clinician acknowledgement is not yet recorded."
        ),
    )
    tracking = b.task_resource(
        "referral-tracker",
        order,
        status="in-progress",
        description="Await review acknowledgement before closing the referral loop.",
    )
    linkage = b.emit(
        "imaging_report_linkage",
        "referral coordinator",
        "Find the study underlying the final report and return modality, study_status and report_conclusion. Availability of images is separate from clinician acknowledgement.",
        {
            "modality": "DX",
            "study_status": "available",
            "report_conclusion": report["conclusion"],
        },
        [order, report, study],
        chain=[("patient", "order"), ("order", "report"), ("report", "study")],
    )
    # Discovery variant supplies only an order identifier. Report/study IDs must
    # be discovered through the order-report-study chain; includes can pack calls.
    begin, end = (
        linkage["prompt"].index("Case paperwork lists:"),
        linkage["prompt"].index(
            ".\n", linkage["prompt"].index("Case paperwork lists:")
        ),
    )
    linkage["prompt"] = (
        linkage["prompt"][:begin]
        + "Case paperwork lists only ServiceRequest business identifier "
        + b.ident(order)
        + linkage["prompt"][end:]
    )
    linkage["reference_steps"][2:] = [
        {"method": "GET", "path": "DiagnosticReport?based-on=" + ref(order)},
        {"method": "GET", "path": ref(study)},
    ]
    linkage["dependency_edges"] = [
        ("supplied_mrn", "patient"),
        ("supplied_order_identifier", "order"),
        ("order", "report"),
        ("report", "study"),
    ]
    linkage["information_regime"] = "order_identifier_only"
    b.emit(
        "referral_loop_closure",
        "referral coordinator",
        "Review the specialist reply. Close the tracking Task only if acknowledgement by the referring clinician is documented; delivery alone is insufficient. Otherwise leave it in progress. Return acknowledged and tracking_status.",
        {
            "acknowledged": acknowledged,
            "tracking_status": "completed" if acknowledged else "in-progress",
        },
        [tracking, note, report],
        updates=[(tracking, {"status": "completed"})] if acknowledged else [],
        variant=b.variant,
        write_contract="If the specialist reply records referring-clinician acknowledgement, set only the tracking Task status to completed using its current ETag. Otherwise make no chart changes.",
    )
    b.tasks[-1]["writable_types"] = ["Task"]  # same capability surface in both branches
    if acknowledged:
        log = {
            "resourceType": "Communication",
            "status": "completed",
            "subject": b.patient,
            "about": [reference(report)],
            "sent": b.day(),
            "payload": [
                {
                    "contentString": "Documented referring-clinician acknowledgement reconciled."
                }
            ],
        }
        b.emit(
            "acknowledgement_and_task_completion",
            "referral coordinator",
            "Reconcile the documented acknowledgement: add the communication log and complete the tracking task in one transaction. Return acknowledgement_logged=true and tracking_completed=true.",
            {"acknowledgement_logged": True, "tracking_completed": True},
            [tracking, note, report],
            creates=[log],
            updates=[(tracking, {"status": "completed"})],
            atomic=True,
        )


def medication(b):
    b.module = "medication"
    # Document an already-authorized episode; no generated dose is presented as advice.
    med = b.resource(
        "episode-medication",
        "Medication",
        status="active",
        code={"text": "Acetaminophen oral preparation"},
    )
    rx = b.resource(
        "episode-prescription",
        "MedicationRequest",
        status="active",
        intent="order",
        subject=b.patient,
        medicationReference=reference(med),
        authoredOn=b.day(-7),
        requester=reference(b.h["practitioner"]),
        note=[
            {
                "text": "Existing clinician prescription for the synthetic acute-care episode. Dose verification remains outside this retrieval task."
            }
        ],
    )
    dispense = b.resource(
        "episode-dispense",
        "MedicationDispense",
        status="completed",
        subject=b.patient,
        medicationReference=reference(med),
        authorizingPrescription=[reference(rx)],
        whenPrepared=b.day(-6),
        whenHandedOver=b.day(-5),
    )
    admin = b.resource(
        "episode-administration",
        "MedicationAdministration",
        status="not-done",
        subject=b.patient,
        medicationReference=reference(med),
        request=reference(rx),
        effectiveDateTime=b.day(-4),
        statusReason=[{"text": "Patient declined this observed dose."}],
    )
    b.variant = "dispensed_but_observed_dose_declined"
    b.emit(
        "medication_supply_vs_administration",
        "medication reconciliation nurse",
        "A relative assumes the prescription was taken because the pharmacy dispensed it. Compare the prescription, handover and observed administration record. Return dispensed, observed_dose_given and reason. Do not infer home adherence.",
        {
            "dispensed": True,
            "observed_dose_given": False,
            "reason": "Patient declined this observed dose.",
        },
        [rx, med, dispense, admin],
        chain=[
            ("patient", "prescription"),
            ("prescription", "dispense"),
            ("prescription", "administration"),
        ],
    )
    message = b.resource(
        "refill-call",
        "Communication",
        status="completed",
        subject=b.patient,
        sent=b.day(),
        sender=b.patient,
        about=[reference(rx)],
        payload=[
            {
                "contentString": "Caller requests more medication but cannot confirm current instructions. Route to the prescribing team for verification; no renewal has been authorized."
            }
        ],
    )
    review = b.review(
        "refill",
        message,
        "Verify medication instructions with prescribing team before renewal.",
    )
    b.emit(
        "refill_request_routing",
        "telephone triage nurse",
        "The caller asks for another supply but cannot confirm current instructions. Create one review request linked to the call. Do not issue a prescription or claim the refill is approved. Return review_requested=true and prescription_changed=false.",
        {"review_requested": True, "prescription_changed": False},
        [message, rx],
        creates=[review],
        conditional=True,
    )


def immunization(b):
    b.module = "immunization"
    verified = b.rng.choice([True, False])
    b.variant = "outside_record_verified" if verified else "patient_report_date_unknown"
    vaccine = {"text": "Seasonal influenza vaccine; product not documented"}
    event_day = b.day(-35)[:10]
    report = b.document(
        "vaccine-source",
        "Outside immunization information",
        (
            f"Outside clinic administration record reviewed: seasonal influenza vaccine on {event_day}; product and lot unavailable."
            if verified
            else "Patient/caregiver recalls an influenza vaccine last autumn. Exact date, product, lot and outside administration record are unavailable. Record as reported history only."
        ),
    )
    immunization = {
        "resourceType": "Immunization",
        "status": "completed",
        "patient": b.patient,
        "vaccineCode": vaccine,
        "primarySource": False,
        "reportOrigin": {
            "text": "Outside clinic record" if verified else "Patient/caregiver recall"
        },
        **(
            {"occurrenceDateTime": event_day}
            if verified
            else {"occurrenceString": "last autumn; exact date unknown"}
        ),
    }
    b.emit(
        "outside_immunization_documentation",
        "immunization nurse",
        "Transcribe the available outside vaccination history. Preserve its source and date uncertainty; do not invent a product, lot, exact date or an in-clinic administration. Return recorded=true, exact_date_known and primary_source=false.",
        {"recorded": True, "exact_date_known": verified, "primary_source": False},
        [report],
        creates=[immunization],
        write_contract='Create one Immunization with status="completed", patient=the resolved Patient, vaccineCode={"text":"Seasonal influenza vaccine; product not documented"}, and primarySource=false. If an outside clinic administration record supplies an exact date, use occurrenceDateTime with that date and reportOrigin={"text":"Outside clinic record"}. If there is only recall, use occurrenceString="last autumn; exact date unknown" and reportOrigin={"text":"Patient/caregiver recall"}. Do not add unsupported fields.',
    )
    duplicate = b.resource(
        "duplicate-immunization",
        "Immunization",
        status="completed",
        patient=b.patient,
        vaccineCode={"text": "Tetanus vaccine; product not documented"},
        occurrenceDateTime=event_day,
        primarySource=False,
        reportOrigin={"text": "Outside clinic record"},
    )
    correction = b.document(
        "vaccine-correction",
        "Immunization correction instruction",
        "Registry reconciliation confirms the listed immunization entry was assigned to this chart in error. Preserve the audit trail by marking entered-in-error, not deleting it. This does not establish the patient's vaccine status.",
    )
    b.emit(
        "immunization_error_correction",
        "immunization registrar",
        "The documented reconciliation instruction identifies an erroneous vaccine entry. Mark that entry entered-in-error and retain its clinical history. Return status and vaccine_status_established=false.",
        {"status": "entered-in-error", "vaccine_status_established": False},
        [duplicate, correction],
        updates=[(duplicate, {"status": "entered-in-error"})],
    )


def revenue(b):
    b.module = "revenue"
    coverage = b.h["coverage"]
    eligible = b.rng.choice([True, False])
    b.variant = "in_force" if eligible else "inactive_on_service_date"
    request = b.resource(
        "eligibility-request",
        "CoverageEligibilityRequest",
        status="active",
        purpose=["validation"],
        patient=b.patient,
        created=b.day(-2),
        servicedDate=b.at.isoformat(),
        insurer=reference(b.h["payer"]),
        insurance=[{"focal": True, "coverage": reference(coverage)}],
    )
    response = b.resource(
        "eligibility-response",
        "CoverageEligibilityResponse",
        status="active",
        purpose=["validation"],
        patient=b.patient,
        created=b.day(-1),
        servicedDate=b.at.isoformat(),
        request=reference(request),
        outcome="complete",
        insurer=reference(b.h["payer"]),
        insurance=[{"coverage": reference(coverage), "inforce": eligible}],
    )
    b.emit(
        "date_specific_eligibility",
        "insurance verification specialist",
        "The registration screen labels the policy active. Check the insurer's response for the planned service date. Return service_date, in_force and payment_guaranteed=false. The Coverage status alone is insufficient.",
        {
            "service_date": b.at.isoformat(),
            "in_force": eligible,
            "payment_guaranteed": False,
        },
        [coverage, request, response],
        chain=[
            ("patient", "coverage"),
            ("coverage", "eligibility_request"),
            ("eligibility_request", "response"),
        ],
    )
    account = b.resource(
        "visit-account",
        "Account",
        status="active",
        subject=[b.patient],
        name="Synthetic outpatient visit account",
        coverage=[{"coverage": reference(coverage), "priority": 1}],
    )
    charge = b.resource(
        "duplicate-charge",
        "ChargeItem",
        status="billable",
        subject=b.patient,
        code=cc("office-service", "Synthetic outpatient service"),
        account=[reference(account)],
        quantity={"value": 1},
        occurrenceDateTime=b.day(-3),
    )
    proof = b.document(
        "billing-correction",
        "Billing audit instruction",
        "The listed charge was entered twice in error. Mark this duplicate charge entered-in-error. Retain the account and underlying clinical records. This correction does not void another legitimate service.",
    )
    b.emit(
        "duplicate_charge_correction",
        "billing specialist",
        "Follow the billing audit instruction to correct the duplicate charge. Do not delete clinical records or close the account. Return charge_status and account_status.",
        {"charge_status": "entered-in-error", "account_status": "active"},
        [charge, account, proof],
        updates=[(charge, {"status": "entered-in-error"})],
    )
    claim = b.resource(
        "unsubmitted-claim",
        "Claim",
        status="draft",
        type=cc(
            "professional", system="http://terminology.hl7.org/CodeSystem/claim-type"
        ),
        use="claim",
        patient=b.patient,
        created=b.day(-1),
        provider=reference(b.h["organization"]),
        priority=cc(
            "normal", system="http://terminology.hl7.org/CodeSystem/processpriority"
        ),
        insurance=[{"sequence": 1, "focal": True, "coverage": reference(coverage)}],
    )
    b.deletable.append(ref(claim))
    b.emit(
        "delete_unsubmitted_duplicate_claim",
        "billing specialist",
        "The billing team explicitly authorizes removal of this duplicate, unsubmitted draft claim. Confirm draft status and delete only it; retain insurance and account records. Submitted claims would require a different correction workflow. Return deleted=true and clinical_records_changed=false.",
        {"deleted": True, "clinical_records_changed": False},
        [claim, account, coverage],
        deletes=[claim],
    )


def equipment(b):
    b.module = "equipment"
    device = b.resource(
        "home-monitor",
        "Device",
        status="active",
        patient=b.patient,
        type={
            "text": "Home pediatric weight scale"
            if b.source["age"] < 2
            else "Home weight scale"
        },
    )
    request = b.resource(
        "equipment-order",
        "DeviceRequest",
        status="active",
        intent="order",
        subject=b.patient,
        codeReference=reference(device),
        authoredOn=b.day(-8),
        requester=reference(b.h["practitioner"]),
    )
    delivered = b.rng.choice([True, False])
    b.variant = "delivered_training_pending" if delivered else "delivery_in_progress"
    delivery = b.resource(
        "equipment-delivery",
        "SupplyDelivery",
        status="completed" if delivered else "in-progress",
        patient=b.patient,
        suppliedItem={"quantity": {"value": 1}, "itemReference": reference(device)},
        **({"occurrenceDateTime": b.day(-2)} if delivered else {}),
    )
    use = b.resource(
        "equipment-use",
        "DeviceUseStatement",
        status="intended",
        subject=b.patient,
        device=reference(device),
        basedOn=[reference(request)],
        recordedOn=b.day(-1),
        note=[{"text": "Training and observed use have not yet been documented."}],
    )
    b.emit(
        "home_equipment_handoff",
        "home-health coordinator",
        "Check the equipment order, delivery and use documentation. Request teaching only after confirmed delivery; otherwise request a delivery-status follow-up. Do not report that the device is already in use. Return delivered, use_confirmed=false and followup (teaching or delivery).",
        {
            "delivered": delivered,
            "use_confirmed": False,
            "followup": "teaching" if delivered else "delivery",
        },
        [request, device, delivery, use],
        write_contract=b.review_contract(
            request,
            'If delivery is completed, description="Arrange device teaching."; otherwise description="Check pending equipment delivery."',
        ),
        creates=[
            b.review(
                "equipment",
                request,
                "Arrange device teaching."
                if delivered
                else "Check pending equipment delivery.",
            )
        ],
        chain=[
            ("patient", "device_request"),
            ("device_request", "device"),
            ("device", "delivery"),
            ("device", "use_statement"),
        ],
    )
    diet = b.resource(
        "nutrition-order",
        "NutritionOrder",
        status="active",
        intent="order",
        patient=b.patient,
        dateTime=b.day(-4),
        oralDiet={
            "type": [
                {
                    "text": "Age-appropriate feeding plan"
                    if b.source["age"] < 1
                    else "Regular diet"
                }
            ],
            "instruction": "Use the documented meal plan; verify any change with the ordering team.",
        },
    )
    report = b.resource(
        "diet-call",
        "Communication",
        status="completed",
        subject=b.patient,
        sent=b.day(-1),
        payload=[
            {
                "contentString": "Caller recalls outside instructions to change the meal plan but cannot provide the written order. Current signed nutrition order remains unchanged pending clarification."
            }
        ],
        about=[reference(diet)],
    )
    b.emit(
        "nutrition_order_discrepancy",
        "care coordinator",
        "Compare the active nutrition order with the unverified outside instructions. Route the discrepancy for clarification and preserve the signed order. Return clarification_requested=true and order_changed=false.",
        {"clarification_requested": True, "order_changed": False},
        [diet, report],
        creates=[
            b.review(
                "nutrition",
                report,
                "Clarify reported outside nutrition instructions with ordering team.",
            )
        ],
    )


def records(b):
    b.module = "records"
    active = b.rng.choice([True, False])
    b.variant = "scope_valid" if active else "scope_expired"
    outside = b.resource(
        "recipient-organization",
        "Organization",
        active=True,
        name="Synthetic receiving practice",
    )
    consent = b.resource(
        "release-consent",
        "Consent",
        status="active",
        scope=cc(
            "patient-privacy",
            system="http://terminology.hl7.org/CodeSystem/consentscope",
        ),
        category=[cc("record-release", "Benchmark records release")],
        patient=b.patient,
        dateTime=b.day(-15),
        policyRule=cc(
            "fictional-local-policy", "Fictional workflow policy; not legal advice"
        ),
        provision={
            "type": "permit",
            "period": {"start": b.day(-14), "end": b.day(10 if active else -2)},
            "actor": [
                {
                    "role": cc("recipient", "Authorized receiving practice"),
                    "reference": reference(outside),
                }
            ],
        },
    )
    doc = b.document(
        "transfer-summary",
        "Transfer summary",
        "Synthetic care coordination summary prepared for the receiving practice. It contains no authorization to release records on its own.",
    )
    request = b.resource(
        "records-request",
        "CommunicationRequest",
        status="active",
        subject=b.patient,
        recipient=[reference(outside)],
        about=[reference(doc)],
        authoredOn=b.day(-1),
        payload=[
            {
                "contentString": "Receiving practice requests the transfer summary. Verify the consent scope and dates before preparing release."
            }
        ],
    )
    review = b.review(
        "release",
        request,
        "Prepare authorized transfer-summary release for staff review."
        if active
        else "Obtain updated records-release authorization before preparation.",
    )
    b.emit(
        "records_release_scope_review",
        "health information management clerk",
        "Under this explicitly fictional local policy, the authorization must name the receiving practice and cover today's date. Read its period and recipient, then create the appropriate staff-review task. Do not send records. Return authorization_current and release_sent=false.",
        {"authorization_current": active, "release_sent": False},
        [request, consent, outside, doc],
        creates=[review],
        write_contract=b.review_contract(
            request,
            'If the named recipient and authorization period are valid today, description="Prepare authorized transfer-summary release for staff review." Otherwise description="Obtain updated records-release authorization before preparation."',
        ),
        chain=[
            ("patient", "release_request"),
            ("release_request", "recipient"),
            ("patient", "consent_scope"),
            ("release_request", "document"),
        ],
    )
    draft = b.resource(
        "duplicate-release-draft",
        "CommunicationRequest",
        status="draft",
        subject=b.patient,
        recipient=[reference(outside)],
        payload=[
            {
                "contentString": "Unsent duplicate release request; explicitly removable by HIM staff."
            }
        ],
    )
    b.deletable.append(ref(draft))
    b.emit(
        "delete_unsent_release_draft",
        "health information management clerk",
        "HIM confirms the listed unsent request is a duplicate draft and explicitly authorizes deleting it. Preserve the active request, authorization, documents and release history. Return duplicate_deleted=true and active_request_preserved=true.",
        {"duplicate_deleted": True, "active_request_preserved": True},
        [draft, request],
        deletes=[draft],
    )


def nursing(b):
    b.module = "nursing"
    b.variant = "pediatric_caregiver" if b.source["age"] < 18 else "adult_self_report"
    caller = reference(b.h["caregiver"]) if b.source["age"] < 18 else b.patient
    message = b.resource(
        "allergy-history-call",
        "Communication",
        status="completed",
        subject=b.patient,
        sender=caller,
        sent=b.day(-1),
        payload=[
            {
                "contentString": "New history reported today: an itchy rash after an antibiotic in the past; drug name and date unknown. No current reaction. This history has not been clinically confirmed."
            }
        ],
    )
    allergy = {
        "resourceType": "AllergyIntolerance",
        "patient": b.patient,
        "clinicalStatus": cc(
            "active",
            system="http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
        ),
        "verificationStatus": cc(
            "unconfirmed",
            system="http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
        ),
        "code": {"text": "Antibiotic, name unknown"},
        "asserter": caller,
        "note": [
            {
                "text": "Reported past itchy rash; drug and date unknown. No current reaction; clinical confirmation pending."
            }
        ],
    }
    b.emit(
        "reported_allergy_with_uncertainty",
        "triage nurse",
        "Record the newly reported allergy history as unconfirmed, including its uncertain drug and date. Do not guess the antibiotic or assert anaphylaxis. Return recorded=true and verification_status=unconfirmed.",
        {"recorded": True, "verification_status": "unconfirmed"},
        [message, *([b.h["caregiver"]] if b.source["age"] < 18 else [])],
        creates=[allergy],
    )
    encounter = b.resource(
        "nurse-visit",
        "Encounter",
        status="in-progress",
        subject=b.patient,
        **{
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
            }
        },
        period={"start": b.day(0, "09:00:00")},
    )
    measurement = round(
        b.h["followup-weight-2"]["valueQuantity"]["value"] + b.rng.uniform(-0.2, 0.2), 1
    )
    worksheet = b.document(
        "nursing-worksheet",
        "Signed nursing worksheet",
        f"Weight measured in clinic: {measurement} kg at {b.day(0, '09:15:00')}. The nurse completed this visit at {b.day(0, '09:30:00')}. Transcribe the measured weight and close only the listed encounter. No interpretation or medication change is requested.",
        offset=0,
    )
    observation = {
        "resourceType": "Observation",
        "status": "final",
        "subject": b.patient,
        "encounter": reference(encounter),
        "code": cc("29463-7", "Body weight", LOINC),
        "effectiveDateTime": b.day(0, "09:15:00"),
        "valueQuantity": {
            "value": measurement,
            "unit": "kg",
            "system": "http://unitsofmeasure.org",
            "code": "kg",
        },
    }
    b.emit(
        "nursing_observation_and_visit_close",
        "clinic nurse",
        "Transcribe the signed worksheet's measured weight with its unit and measurement time, then close the visit at the documented completion time. Commit both together; preserve previous observations. Return weight_kg and encounter_status=finished.",
        {"weight_kg": measurement, "encounter_status": "finished"},
        [encounter, worksheet],
        creates=[observation],
        write_contract='Create one Observation with status="final", subject=the resolved Patient, encounter=the listed Encounter, code='
        + json.dumps(cc("29463-7", "Body weight", LOINC))
        + ', effectiveDateTime=the worksheet measurement timestamp, and valueQuantity={"value": the measured weight from the worksheet, "unit":"kg", "system":"http://unitsofmeasure.org", "code":"kg"}. Update only Encounter.status to finished and period.end to the worksheet completion timestamp, preserving period.start and all other fields. Add no inferred fields.',
        updates=[
            (
                encounter,
                {
                    "status": "finished",
                    "period": {**encounter["period"], "end": b.day(0, "09:30:00")},
                },
            )
        ],
        atomic=True,
    )


def expand_workflows(source, handles, snapshot, rng, add):
    b = Episode(source, handles, snapshot, rng, add)
    registration(b)
    scheduling(b)
    eligible = list(MODULES)
    if source["age"] < 5:
        eligible = [name for name in eligible if name not in {"medication", "referral"}]
    if source["age"] < 1:
        eligible.remove("immunization")
    selected = sorted(rng.sample(eligible, rng.randint(3, min(5, len(eligible)))))
    for name in selected:
        b.variant = "routine"
        globals()[name](b)
    return {
        "tasks": b.tasks,
        "deletable": b.deletable,
        "modules": ["registration", "scheduling", *selected],
    }
