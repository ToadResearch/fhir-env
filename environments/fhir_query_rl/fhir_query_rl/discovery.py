"""Optional multi-hop variants over existing charts, retaining full CRUD.

No new clinical facts or resources are generated. Every extra answer/evidence
field is read from the same frozen snapshot as the original task.
"""

import copy
import hashlib

from .dataset import MRN_SYSTEM, annotate_dependencies
from .validation import ref


FAMILIES = {
    "referral_loop_closure": "referral_closure_discovery",
    "acknowledgement_and_task_completion": "referral_acknowledgement_discovery",
    "delete_unsubmitted_duplicate_claim": "claim_cleanup_discovery",
}


def discovery_variant(task, resources):
    if task["family"] not in FAMILIES:
        return None
    world = {ref(r): r for r in resources}
    evidence = {key: world[key] for key in task["gold"]["evidence"]}
    patient = world["Patient/" + task["patient_id"]]
    mrn = next(v["value"] for v in patient["identifier"] if v["system"] == MRN_SYSTEM)
    variant = copy.deepcopy(task)
    variant["id"] = hashlib.sha256((task["id"] + ":discovery-v1").encode()).hexdigest()[
        :24
    ]
    variant["family"] = FAMILIES[task["family"]]
    variant["base_task_id"] = task["id"]
    variant["base_scenario_variant"] = task.get("scenario_variant", "baseline")
    variant["scenario_variant"] = "discovery-v1"
    variant["information_regime"] = "mrn_and_clinical_context"
    variant["requires_read_before_write"] = True
    patient_read = {"method": "GET", "path": f"Patient?identifier={MRN_SYSTEM}|{mrn}"}
    writes = [s for s in task["reference_steps"] if s["method"] != "GET"]

    if task["family"] == "delete_unsubmitted_duplicate_claim":
        claim = next(r for r in evidence.values() if r["resourceType"] == "Claim")
        coverage = next(r for r in evidence.values() if r["resourceType"] == "Coverage")
        account = next(r for r in evidence.values() if r["resourceType"] == "Account")
        claim_id = claim["identifier"][0]["value"]
        # Authorization still identifies exactly one draft. Coverage and account
        # identifiers are withheld from the request and discovered in the chart.
        reads = [
            patient_read,
            {
                "method": "GET",
                "path": f"Claim?patient={patient['id']}&identifier={claim_id}&status=draft",
            },
            {"method": "GET", "path": ref(coverage)},
            {"method": "GET", "path": f"Account?patient={patient['id']}&status=active"},
        ]
        variant["prompt"] = (
            f"For verified caller MRN {mrn} (system {MRN_SYSTEM}), billing staff authorize deletion of the "
            f"unsubmitted duplicate Claim with business identifier {claim_id}. Resolve the patient, inspect "
            "that draft's linked insurance Coverage, and find the active patient Account using that same "
            "coverage. Delete only that named unreferenced draft with its current ETag. Preserve the "
            "account, coverage and all clinical records. Return deleted=true, clinical_records_changed=false, "
            "coverage_status and account_status, with supporting evidence."
        )
        variant["gold"]["answer"].update(
            coverage_status=coverage["status"], account_status=account["status"]
        )
        variant["information_regime"] = "mrn_and_authorized_claim_identifier"
        edges = [
            ("supplied_mrn", "patient"),
            ("patient", "claim"),
            ("claim", "coverage"),
            ("coverage", "account"),
        ]
    else:
        tracking = next(r for r in evidence.values() if r["resourceType"] == "Task")
        order = world[tracking["focus"]["reference"]]
        report = next(
            r for r in evidence.values() if r["resourceType"] == "DiagnosticReport"
        )
        study = world[report["imagingStudy"][0]["reference"]]
        note = next(
            r for r in evidence.values() if r["resourceType"] == "DocumentReference"
        )
        coding = order["code"]["coding"][0]
        note_code = note["type"]["coding"][0]
        reads = [
            patient_read,
            {
                "method": "GET",
                "path": f"ServiceRequest?patient={patient['id']}&code={coding['system']}|{coding['code']}&status={order['status']}",
            },
            {
                "method": "GET",
                "path": f"DiagnosticReport?patient={patient['id']}&based-on={ref(order)}&status=final",
            },
            {"method": "GET", "path": ref(study)},
            {
                "method": "GET",
                "path": f"Task?patient={patient['id']}&focus={ref(order)}&status={tracking['status']}",
            },
            {
                "method": "GET",
                "path": f"DocumentReference?patient={patient['id']}&type={note_code['system']}|{note_code['code']}",
            },
        ]
        variant["gold"]["evidence"] = sorted(
            set(task["gold"]["evidence"]) | {ref(order), ref(study)}
        )
        variant["gold"]["target_evidence"] = [
            k for k in variant["gold"]["evidence"] if not k.startswith("Patient/")
        ]
        variant["gold"]["evidence_fields"][ref(order)] = ["code", "status", "subject"]
        variant["gold"]["evidence_fields"][ref(study)] = [
            "modality",
            "status",
            "basedOn",
        ]
        variant["gold"]["answer"].update(
            modality=study["modality"][0]["code"],
            study_status=study["status"],
            report_conclusion=report["conclusion"],
        )
        variant["prompt"] = (
            f"As of {task['snapshot_date']}, assist the referral coordinator for MRN {mrn} (system {MRN_SYSTEM}). "
            f"Locate the {order['code']['text']} order with status {order['status']} "
            f"(code {coding['system']}|{coding['code']}), its final report, underlying imaging study, tracking "
            f"Task with status {tracking['status']}, and specialist reply (document type "
            f"{note_code['system']}|{note_code['code']}). Discover their references from the chart; "
            "case paperwork supplies no resource IDs or secondary business identifiers. "
        )
        if task["family"] == "referral_loop_closure":
            variant["prompt"] += (
                "If the reply explicitly records referring-clinician acknowledgement, complete the tracking "
                "Task by changing status only; return acknowledged=true and tracking_status=completed. "
                "If it records delivery without acknowledgement, preserve the Task and return acknowledged=false "
                "and tracking_status=in-progress. Delivery alone is insufficient. "
            )
        else:
            pattern = task["gold"]["creates"][0]
            variant["prompt"] += (
                "The reply documents acknowledgement. Complete the tracking Task and log that acknowledgement "
                "in one atomic transaction. Create one completed Communication with the resolved patient as "
                f"subject, the retrieved report in about, sent={pattern['sent']}, and payload.contentString="
                f"'{pattern['payload'][0]['contentString']}'. Preserve all other fields and use current ETags. "
                "Return acknowledgement_logged=true and tracking_completed=true. "
            )
        variant["prompt"] += (
            "Also return modality, study_status and report_conclusion from the linked evidence."
        )
        edges = [
            ("supplied_mrn", "patient"),
            ("patient", "order"),
            ("order", "report"),
            ("report", "study"),
            ("order", "tracking_task"),
            ("patient", "reply"),
        ]
    variant["prompt"] += (
        "\nReturn exactly answer/evidence JSON. Cite only retrieved records supporting the answer and requested changes."
    )
    variant["dependency_edges"] = edges
    variant["reference_steps"] = reads + writes
    variant["reference_calls"] = len(reads + writes)
    annotate_dependencies(variant)
    return variant


def add_discovery_variants(tasks, read_resources):
    """Append deterministic variants without replacing any original CRUD case."""
    variants = [
        discovery_variant(t, read_resources(t))
        for t in tasks
        if t["family"] in FAMILIES
    ]
    return tasks + [t for t in variants if t is not None]
