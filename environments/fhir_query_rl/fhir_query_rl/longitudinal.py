"""Seeded, source-aware follow-up episodes with explicit reported/documented distinctions.

These authored templates are synthetic workflow fixtures, not clinician-validated histories.
"""

import base64
from datetime import timedelta


def expand_chart(source, handles, snapshot, rng, add):
    from .dataset import cc, reference

    patient, practitioner = handles["patient"], handles["practitioner"]
    organization = handles["organization"]
    variant = rng.choice(
        ["care_transition", "outpatient_followup", "transfer_coordination"]
    )
    context = {
        "care_transition": "Follow-up after a reported outside urgent visit. The outside discharge list is not yet reconciled.",
        "outpatient_followup": "Serial outpatient follow-up for reported intermittent fatigue; no cause is inferred from the laboratory value.",
        "transfer_coordination": "Transfer of care between clinics. The patient knows some medication names but does not have a complete prior treatment list.",
    }[variant]
    age = source["age"]
    reporter = handles["caregiver"] if age < 18 else patient
    reporter_label = "Caregiver" if age < 18 else "Patient"
    if age < 12:
        context = "Caregiver coordinates pediatric follow-up after a reported outside visit. Prior instructions and medication use require verification; no diagnosis is inferred."
    barrier = rng.choice(
        [
            "cannot take weekday morning calls",
            "needs appointment transport arranged",
            "prefers a written medication list",
            "relies on a family member to organize documents",
        ]
    )
    known_conditions = [str(x) for x in source["profile"].get("chronic_conditions", [])]
    condition_text = (
        "; ".join(known_conditions)
        if known_conditions
        else "No chronic condition names are present in the imported profile; this does not establish absence."
    )
    medication_handles = [
        r
        for key, r in handles.items()
        if key.startswith("medication-") and r["resourceType"] == "MedicationStatement"
    ]
    selected_medication = medication_handles[0] if medication_handles else None
    medication_name = (
        selected_medication["medicationCodeableConcept"]["text"]
        if selected_medication
        else "an unidentified medication from an outside clinic"
    )
    # Broad synthetic age bands require review; measurements are correlated
    # within a patient rather than independent large swings between visits.
    base_weight = (
        rng.uniform(3.2, 4.0) + age * 6.0
        if age < 1
        else 8.5 + age * 2.3 + rng.uniform(-1, 1)
        if age < 6
        else 20 + (age - 6) * 3.0 + rng.uniform(-2.5, 2.5)
        if age < 18
        else rng.uniform(58, 93)
    )
    base_pulse = (
        rng.randint(115, 145)
        if age < 1
        else rng.randint(90, 120)
        if age < 6
        else rng.randint(70, 100)
        if age < 18
        else rng.randint(62, 88)
    )

    def note(label, title, text, when, encounter=None, code="longitudinal-note"):
        return add(
            label,
            "DocumentReference",
            origin="generated-longitudinal",
            status="current",
            subject=reference(patient),
            type=cc(code),
            date=when.isoformat() + "T12:00:00Z",
            author=[reference(practitioner)],
            **({"context": {"encounter": [reference(encounter)]}} if encounter else {}),
            content=[
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "data": base64.b64encode(text.encode()).decode(),
                        "title": title,
                    }
                }
            ],
        )

    episode = add(
        "longitudinal-episode",
        "EpisodeOfCare",
        origin="generated-longitudinal",
        status="active",
        patient=reference(patient),
        managingOrganization=reference(organization),
        period={"start": (snapshot - timedelta(days=40)).isoformat()},
        type=[cc(variant)],
    )
    team = add(
        "longitudinal-team",
        "CareTeam",
        origin="generated-longitudinal",
        status="active",
        subject=reference(patient),
        name="Synthetic follow-up coordination team",
        participant=[
            {"member": reference(practitioner)},
            {"member": reference(handles["caregiver"])},
        ],
    )
    add(
        "reported-home-device",
        "Device",
        origin="generated-longitudinal",
        status="active",
        patient=reference(patient),
        type={"text": "Home weight scale, patient reported"},
        note=[
            {
                "text": "Reported ownership; no independently verified measurements are inferred."
            }
        ],
    )
    add(
        "contact-consent",
        "Consent",
        origin="generated-longitudinal",
        status="active",
        scope=cc("patient-privacy"),
        category=[cc("contact-preference")],
        patient=reference(patient),
        dateTime=(snapshot - timedelta(days=40)).isoformat() + "T12:00:00Z",
        policyRule={
            "text": "Fictional permission to contact the designated synthetic caregiver for scheduling only."
        },
    )
    profile = note(
        "authored-profile",
        "Synthetic follow-up profile",
        f"GENERATED FOLLOW-UP CONTEXT; source records are unchanged.\n{context}\nImported condition names: {condition_text}\nReported logistical need: {barrier}.\nThe imported medication list is a historical chart list, not verified current use. Do not resolve discrepancies by assuming either source is definitive.",
        snapshot - timedelta(days=40),
        code="synthetic-followup-profile",
    )
    history = []
    narratives = [
        f"Intake: {context} Identity checked against the synthetic MRN. Outside records requested. The patient reports {barrier}. Imported condition names were reviewed without adding new diagnoses.",
        "Follow-up: laboratory collection and a patient phone contact are documented separately. Review of the chart medication list is incomplete; patient-reported use requires reconciliation. A laboratory trend alone does not establish diagnosis or treatment response.",
        "Handoff: recent laboratory report is available. A patient-reported medication discrepancy remains open for clinician review. The coordination team must distinguish a missing outside record from a negative clinical finding and preserve both the original list and the report.",
    ]
    for i, days in enumerate([35, 24, 12]):
        when = snapshot - timedelta(days=days)
        encounter = add(
            f"followup-visit-{i}",
            "Encounter",
            origin="generated-longitudinal",
            status="finished",
            **{
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                }
            },
            subject=reference(patient),
            episodeOfCare=[reference(episode)],
            period={"start": when.isoformat(), "end": when.isoformat()},
            participant=[{"individual": reference(practitioner)}],
            type=[cc("followup-coordination")],
        )
        visit_note = note(
            f"followup-note-{i}",
            f"Follow-up visit {i+1}",
            narratives[i],
            when,
            encounter,
        )
        add(
            f"followup-contact-{i}",
            "Communication",
            origin="generated-longitudinal",
            status="completed",
            subject=reference(patient),
            encounter=reference(encounter),
            sent=when.isoformat() + "T16:00:00Z",
            sender=reference(reporter),
            recipient=[reference(practitioner)],
            payload=[
                {
                    "contentString": f"{reporter_label} report: {barrier}. No medication change is authorized by this message."
                }
            ],
        )
        for label, value, unit, ucum in [
            ("weight", round(base_weight + rng.uniform(-0.2, 0.2), 1), "kg", "kg"),
            ("pulse", base_pulse + rng.randint(-4, 4), "beats/minute", "/min"),
            (
                "temperature",
                round(rng.uniform(36.3, 37.2), 1),
                "degrees Celsius",
                "Cel",
            ),
        ]:
            add(
                f"followup-{label}-{i}",
                "Observation",
                origin="generated-longitudinal",
                status="final",
                subject=reference(patient),
                encounter=reference(encounter),
                code=cc(f"measured-{label}", f"Synthetic office {label}"),
                effectiveDateTime=when.isoformat(),
                valueQuantity={
                    "value": value,
                    "unit": unit,
                    "system": "http://unitsofmeasure.org",
                    "code": ucum,
                },
            )
        add(
            f"followup-composition-{i}",
            "Composition",
            origin="generated-longitudinal",
            status="final",
            type=cc("followup-note"),
            subject=reference(patient),
            encounter=reference(encounter),
            date=when.isoformat() + "T12:00:00Z",
            author=[reference(practitioner)],
            title="Synthetic coordination note",
            section=[{"title": "Documented visit", "entry": [reference(visit_note)]}],
        )
        if i < 2:
            lab_value = round(
                max(
                    6.0,
                    handles["lab-result"]["valueQuantity"]["value"]
                    + rng.choice([-1.2, -0.6, 0.4, 0.9]),
                ),
                1,
            )
            order = add(
                f"trend-order-{i}",
                "ServiceRequest",
                origin="generated-longitudinal",
                status="completed",
                intent="order",
                subject=reference(patient),
                encounter=reference(encounter),
                authoredOn=when.isoformat(),
                code=cc("718-7", "Hemoglobin", system="http://loinc.org"),
            )
            specimen = add(
                f"trend-specimen-{i}",
                "Specimen",
                origin="generated-longitudinal",
                status="available",
                subject=reference(patient),
                request=[reference(order)],
                collection={"collectedDateTime": when.isoformat()},
            )
            result = add(
                f"trend-result-{i}",
                "Observation",
                origin="generated-longitudinal",
                status="final",
                subject=reference(patient),
                encounter=reference(encounter),
                specimen=reference(specimen),
                basedOn=[reference(order)],
                code=cc("718-7", "Hemoglobin", system="http://loinc.org"),
                effectiveDateTime=when.isoformat(),
                valueQuantity={
                    "value": lab_value,
                    "unit": "g/dL",
                    "system": "http://unitsofmeasure.org",
                    "code": "g/dL",
                },
            )
            add(
                f"trend-report-{i}",
                "DiagnosticReport",
                origin="generated-longitudinal",
                status="final",
                subject=reference(patient),
                encounter=reference(encounter),
                basedOn=[reference(order)],
                specimen=[reference(specimen)],
                result=[reference(result)],
                code=cc("718-7", "Hemoglobin", system="http://loinc.org"),
                effectiveDateTime=when.isoformat(),
            )
            history.append(result)

    add(
        "reconciliation-list",
        "List",
        origin="generated-longitudinal",
        status="current",
        mode="snapshot",
        subject=reference(patient),
        source=reference(practitioner),
        date=(snapshot - timedelta(days=12)).isoformat() + "T12:00:00Z",
        title="Imported medication entries awaiting reconciliation",
        code=cc("medication-reconciliation"),
        **(
            {"entry": [{"item": reference(r)} for r in medication_handles]}
            if medication_handles
            else {"emptyReason": {"text": "Imported medication entries unavailable"}}
        ),
    )
    # Dispense records are authored events, never reinterpreted as verified patient adherence.
    if selected_medication:
        add(
            "synthetic-dispense",
            "MedicationDispense",
            origin="generated-longitudinal",
            status="completed",
            subject=reference(patient),
            medicationCodeableConcept={"text": medication_name},
            whenHandedOver=(snapshot - timedelta(days=30)).isoformat() + "T12:00:00Z",
            note=[
                {
                    "text": "Fictional pharmacy supply record; dispensing does not establish current use."
                }
            ],
        )
    discrepancy = add(
        "medication-discrepancy",
        "Communication",
        origin="generated-longitudinal",
        status="completed",
        subject=reference(patient),
        sender=reference(reporter),
        recipient=[reference(practitioner)],
        sent=(snapshot - timedelta(days=3)).isoformat() + "T10:00:00Z",
        category=[cc("medication-discrepancy")],
        identifier=[
            {
                "system": "https://fhir-workflows.example/communication",
                "value": "RECON-" + patient["id"][:10],
            }
        ],
        payload=[
            {
                "contentString": f"{reporter_label} says the patient is no longer taking {medication_name} after an outside visit. They cannot recall the date or who advised this. This is an unverified report, not an authorized discontinuation; retain the chart list and route for medication reconciliation."
            }
        ],
    )
    note(
        "reconciliation-note",
        "Medication reconciliation pending",
        f"{medication_name}: patient reports not taking it. Historical imported list and newly authored patient report disagree. An outside discharge medication list is not confirmed. Reconciliation is pending; no prescribing decision is made.",
        snapshot - timedelta(days=3),
        code="medication-reconciliation-note",
    )
    add(
        "care-handoff-task",
        "Task",
        origin="generated-longitudinal",
        status="requested",
        intent="order",
        **{"for": reference(patient)},
        focus=reference(profile),
        owner=reference(team),
        description="Obtain outside records and arrange follow-up using reported contact preferences.",
    )
    return {
        "variant": variant,
        "trend_start": (snapshot - timedelta(days=40)).isoformat(),
        "trend_results": history + [handles["lab-result"]],
        "discrepancy": discrepancy,
        "reported_medication": medication_name,
        "source_medication": selected_medication,
    }
