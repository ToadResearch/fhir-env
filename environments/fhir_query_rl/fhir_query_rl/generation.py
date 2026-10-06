"""Versioned augmentation of the existing benchmark, preserving its task coverage.

Patient-local events, serialization, task oracles and lineage are separate. The
original corpus is an immutable input. No Synthea, model API or clinical decision
engine is used here; all new episodes are authored structural/query fixtures.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import html
import json
import random
import tempfile
import multiprocessing
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path

from .dataset import (
    MRN_SYSTEM,
    ORIGIN_SYSTEM,
    annotate_dependencies,
    build_dataset,
    cc,
    load_jsonl,
    reference,
    stable_id,
    write_jsonl,
)
from .generation_pilot import Compiler, make_tasks, plan_episode, validate_contracts
from .validation import patient_ref, ref, references, schema, validate_graph

VERSION = "0.4.0"
ID_SYSTEM = "https://fhir-query-rl.example/identifier"
SHARED = {
    "clinic",
    "laboratory",
    "payer",
    "clinician",
    "clinician-role",
    "clinic-location",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remap(value, mapping):
    """Replace exact references, including task bodies, without editing prose."""
    if isinstance(value, dict):
        return {k: remap(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def repair_legacy(resources, tasks):
    """Correct known R4 defects; do not reinterpret source notes or profiles."""
    repaired = []
    index = {ref(r): r for r in resources}
    for r in resources:
        key = ref(r)
        if r["resourceType"] == "AllergyIntolerance" and "clinicalStatus" not in r:
            # The current source-profile allergy list is a reported active list,
            # not a confirmed diagnosis. Preserve that distinction explicitly.
            r["clinicalStatus"] = cc(
                "active",
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
            )
            r["verificationStatus"] = cc(
                "unconfirmed",
                system="http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
            )
            r.setdefault("note", []).append(
                {
                    "text": "Reported in the source profile's current allergy list; not clinically reconciled."
                }
            )
            repaired.append(
                {
                    "ref": key,
                    "repair": "reported_allergy_status",
                    "fields": ["clinicalStatus", "verificationStatus", "note"],
                }
            )
        if r["resourceType"] == "DeviceUseStatement" and r.get("basedOn"):
            invalid = [
                x for x in r["basedOn"] if x["reference"].startswith("DeviceRequest/")
            ]
            if invalid:
                remaining = [x for x in r["basedOn"] if x not in invalid]
                if remaining:
                    r["basedOn"] = remaining
                else:
                    del r["basedOn"]
                provenance = next(
                    (
                        p
                        for p in resources
                        if p["resourceType"] == "Provenance"
                        and p.get("target") == [{"reference": key}]
                    ),
                    None,
                )
                if provenance is None:
                    raise ValueError("Device order repair requires existing lineage")
                provenance.setdefault("entity", []).extend(
                    {"role": "source", "what": x} for x in invalid
                )
                repaired.append(
                    {
                        "ref": key,
                        "repair": "device_order_link_in_provenance",
                        "fields": ["basedOn"],
                        "source_refs": [x["reference"] for x in invalid],
                    }
                )
                for task in tasks:
                    fields = task["gold"].get("evidence_fields", {}).get(key, [])
                    if "basedOn" in fields and "basedOn" not in r:
                        fields.remove("basedOn")

    # PUT bodies must continue preserving every unchanged field after repairs.
    def patch_body(value):
        if isinstance(value, dict):
            if value.get("resourceType") and value.get("id") and ref(value) in index:
                old = index[ref(value)]
                for repair in repaired:
                    if repair["ref"] == ref(value):
                        for field in repair["fields"]:
                            if field in old:
                                value[field] = copy.deepcopy(old[field])
                            else:
                                value.pop(field, None)
            for child in value.values():
                patch_body(child)
        elif isinstance(value, list):
            for child in value:
                patch_body(child)

    for task in tasks:
        patch_body(task["reference_steps"])
    return repaired


def enhanced_plan(patient, snapshot, index, seed, scope):
    p = plan_episode(patient, snapshot, index, seed)
    rng = random.Random(stable_id(VERSION, seed, patient["id"], "timeline"))
    p.update(
        {
            "pipeline_version": VERSION,
            "scope": scope,
            "site": index % 2,
            "timeline_stretch": rng.choice([1, 2, 3]),
            "timeline_offset": rng.randrange(2, 9),
            "voice": rng.randrange(4),
            "transport": rng.choice(
                ["own transport", "family transport", "community transport"]
            ),
            "intake_variant": index % 4,
        }
    )
    # Apply the same monotone mapping to planned and serialized event times.
    p["events"] = [
        {**e, "day": e["day"] * p["timeline_stretch"] - p["timeline_offset"]}
        for e in p["events"]
    ]
    p["events"].extend(
        [
            {"day": -1, "event": "transfer_packet_prepared"},
            {"day": 0, "event": "handoff_awaiting_acknowledgement"},
        ]
    )
    return p


class EpisodeCompiler(Compiler):
    """Reuse checked episode semantics with varied timelines and shared facilities."""

    def time(self, day, hour=10):
        day = day * self.plan["timeline_stretch"] - self.plan["timeline_offset"]
        return super().time(day, hour)

    def add(self, label, kind, day=-30, **fields):
        r = super().add(label, kind, day, **fields)
        identity = (
            (self.plan["scope"], self.plan["site"])
            if label in SHARED
            else self.patient["id"]
        )
        r["id"] = stable_id(VERSION, self.plan["seed"], identity, label)
        r["meta"]["tag"] = [{"system": ORIGIN_SYSTEM, "code": "generated-event-graph"}]
        shape = schema()["definitions"][kind]["properties"].get("identifier")
        if shape:
            identifier = {"system": ID_SYSTEM, "value": r["id"][:12].upper()}
            r["identifier"] = (
                [identifier] if shape.get("type") == "array" else identifier
            )
        if label in SHARED:
            site = ("Cedar", "Willow")[self.plan["site"]]
            # These immutable entities have fixed metadata within a shard, so
            # independently compiled patients really share the same resource.
            r["meta"]["lastUpdated"] = "2020-01-01T00:00:00Z"
            if kind == "Organization":
                r["name"] = (
                    site
                    + {
                        "clinic": " Community Clinic",
                        "laboratory": " Reference Laboratory",
                        "payer": " Example Health Plan",
                    }[label]
                )
            elif kind == "Practitioner":
                r["name"] = [{"family": site, "given": ["Morgan"]}]
            elif kind == "Location":
                r["name"] = site + " outpatient room"
        self.events[-1]["ref"] = ref(r)
        self.events[-1]["at"] = r["meta"]["lastUpdated"]
        return r

    def document(self, label, title, text, day):
        # Vary phrasing/context, keeping the episode's explicit facts identical.
        leads = [
            "Telephone coordination record",
            "Care coordination update",
            "Clinic follow-up summary",
            "Administrative handoff note",
        ]
        context = f"{leads[self.plan['voice']]}. Contact is via {('caregiver', 'patient')[self.plan['age'] >= 18]}; planned transport: {self.plan['transport']}. "
        r = super().document(label, title, context + text, day)
        attachment = r["content"][0]["attachment"]
        attachment["data"] = base64.b64encode(
            ("SYNTHETIC BENCHMARK. " + context + text).encode()
        ).decode()
        return r

    def scheduling(self, role):
        super().scheduling(role)
        # A stretched timeline can put the nominal +5 appointment in the past.
        # Use an explicitly future service date, consistently for all references.
        start = (
            (self.at + timedelta(days=7 + self.plan["index"] % 21, hours=10))
            .isoformat()
            .replace("+00:00", "Z")
        )
        end = (
            (
                datetime.fromisoformat(start.replace("Z", "+00:00"))
                + timedelta(minutes=30)
            )
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.by_label["slot"].update(start=start, end=end)
        self.by_label["appointment"].update(start=start, end=end)
        self.by_label["schedule"]["planningHorizon"] = {"start": start, "end": end}
        # Earlier proposed/free versions describe the same reservation window.
        for previous in self.history:
            if ref(previous) in {
                ref(self.by_label["slot"]),
                ref(self.by_label["appointment"]),
            }:
                previous.update(start=start, end=end)

    def insurance(self, payer, org):
        super().insurance(payer, org)
        day = self.by_label["appointment"]["start"][:10]
        for label in ("eligibility-request", "eligibility-response"):
            self.by_label[label]["servicedDate"] = day

    def compile(self):
        compiled = super().compile()
        self.coordination()
        compiled.update(
            resources=self.resources,
            history=self.history,
            events=self.events,
            labels={k: ref(v) for k, v in self.by_label.items()},
        )
        validate_graph([self.patient, *self.resources])
        validate_contracts(self.resources)
        return compiled

    def coordination(self):
        role, clinic = self.by_label["clinician-role"], self.by_label["clinic"]
        service = self.add(
            "coordination-service",
            "HealthcareService",
            -30,
            active=True,
            providedBy=reference(clinic),
            name="Continuity and records coordination",
            location=[reference(self.by_label["clinic-location"])],
        )
        episode = self.add(
            "care-episode",
            "EpisodeOfCare",
            -20,
            status="active",
            patient=self.subject,
            managingOrganization=reference(clinic),
            careManager=reference(role),
            period={"start": self.time(-20)[:10]},
            type=[cc("care-transition", "Care transfer coordination")],
        )
        team = self.add(
            "coordination-team",
            "CareTeam",
            -20,
            status="active",
            subject=self.subject,
            name="Transfer coordination team",
            participant=[
                {
                    "member": reference(self.by_label["clinician"]),
                    "onBehalfOf": reference(clinic),
                }
            ],
            managingOrganization=[reference(clinic)],
        )
        episode["team"] = [reference(team)]
        document = self.document(
            "transfer-summary",
            "Transfer coordination summary",
            "A receiving service has requested a limited packet: laboratory coordination and pharmacy handoff records. Receipt and clinician review are not yet documented.",
            -1,
        )
        items = [self.by_label["lab-note"], self.by_label["medication-note"], document]
        packet = self.add(
            "transfer-list",
            "List",
            -1,
            status="current",
            mode="snapshot",
            subject=self.subject,
            title="Requested transfer packet",
            code=cc("transfer-packet"),
            date=self.time(-1),
            source=reference(role),
            entry=[{"item": reference(r)} for r in items],
        )
        manifest = self.add(
            "transfer-manifest",
            "DocumentManifest",
            -1,
            status="current",
            subject=self.subject,
            created=self.time(-1),
            author=[reference(role)],
            description="Limited transfer packet; receipt not confirmed.",
            content=[reference(r) for r in items],
            recipient=[reference(clinic)],
        )
        task = self.add(
            "handoff-task",
            "Task",
            -1,
            status="requested",
            intent="order",
            **{"for": self.subject},
            focus=reference(episode),
            owner=reference(role),
            authoredOn=self.time(-1),
            code=cc("transfer-handoff"),
            description="Record packet transmission after checking the requested contents.",
            input=[
                {"type": cc("packet-list"), "valueReference": reference(packet)},
                {"type": cc("packet-manifest"), "valueReference": reference(manifest)},
                {"type": cc("receiving-service"), "valueReference": reference(service)},
            ],
        )
        # Patient response is distinct from the clinic's booked appointment state.
        self.add(
            "appointment-response",
            "AppointmentResponse",
            -1,
            appointment=reference(self.by_label["appointment"]),
            actor=self.subject,
            participantStatus="accepted",
            start=self.by_label["appointment"]["start"],
            end=self.by_label["appointment"]["end"],
            comment="Patient confirmed this reservation during booking.",
        )
        issue = self.add(
            "duplicate-review",
            "DetectedIssue",
            -1,
            status="final",
            patient=self.subject,
            code=cc(
                "possible-duplicate-request", "Administrative duplicate-request review"
            ),
            implicated=[
                reference(self.by_label["lab-order"]),
                reference(self.by_label["older-lab-order"]),
            ],
            identifiedDateTime=self.time(-1),
            author=reference(role),
            detail="Both requests have similar descriptions. Dates and accession identifiers differ. A coordinator requested review; neither is automatically cancelled.",
        )
        task["input"].append(
            {"type": cc("review-item"), "valueReference": reference(issue)}
        )
        # Structured intake adds alternate value choices and explicit unknowns.
        choices = [
            {"valueBoolean": False},
            {
                "valueCodeableConcept": cc(
                    "family-assistance", "Family assistance requested"
                )
            },
            {"valueString": "Caller will provide the outside clinic's name later."},
            {
                "dataAbsentReason": cc(
                    "asked-unknown",
                    system="http://terminology.hl7.org/CodeSystem/data-absent-reason",
                )
            },
        ]
        codes = [
            "transport-assistance-requested",
            "transfer-support",
            "receiving-clinic-name",
            "preferred-contact-time",
        ]
        self.add(
            "intake-detail",
            "Observation",
            -1,
            status="final",
            subject=self.subject,
            code=cc(codes[self.plan["intake_variant"]]),
            effectiveDateTime=self.time(-1),
            performer=[self.reporter],
            **choices[self.plan["intake_variant"]],
        )


def workflow_tasks(patient, compiled, shard, split, upstream_split):
    """Compile discovery-based transfer, clerical and review tasks from the chart."""
    index = {ref(r): r for r in [patient, *compiled["resources"]]}
    labels, plan = compiled["labels"], compiled["plan"]
    pkey, pid = ref(patient), patient["id"]
    mrn = next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)
    pstep = {"method": "GET", "path": f"Patient?identifier={MRN_SYSTEM}|{mrn}"}
    tasks = []

    def emit(
        family,
        instruction,
        answer,
        evidence,
        reads,
        edges,
        *,
        updates=(),
        creates=(),
        atomic=False,
        writable_refs=(),
    ):
        evidence = [pkey, *evidence]
        writes = []
        changes = []
        for key, fields in updates:
            resource = index[key]
            writes.append(
                {
                    "method": "PUT",
                    "path": key,
                    "body": {**copy.deepcopy(resource), **fields},
                    "headers": {"If-Match": f'W/"{resource["meta"]["versionId"]}"'},
                }
            )
            changes.extend(
                {"ref": key, "field": field, "value": value}
                for field, value in fields.items()
            )
        writes.extend(
            {"method": "POST", "path": r["resourceType"], "body": r} for r in creates
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
                                    "method": w["method"],
                                    "url": w["path"],
                                    **(
                                        {"ifMatch": w["headers"]["If-Match"]}
                                        if "headers" in w
                                        else {}
                                    ),
                                },
                                "resource": w["body"],
                            }
                            for w in writes
                        ],
                    },
                }
            ]
        steps = [pstep, *reads, *writes]
        task = {
            "id": stable_id(VERSION, pid, family),
            "family": family,
            "split": split,
            "upstream_split": upstream_split,
            "patient_id": pid,
            "snapshot_date": plan["snapshot_date"],
            "shard": shard,
            "prompt": f"For MRN {mrn}, {instruction}\nReturn JSON with answer and evidence (retrieved Type/id references).",
            "domain": "records"
            if "transfer" in family
            else "scheduling"
            if "appointment" in family
            else "diagnostics",
            "role": "care coordinator",
            "scenario_variant": plan["lab_branch"] + ":" + plan["panel_encoding"],
            "omit": [],
            "writable_types": sorted(
                {index[k]["resourceType"] for k, _ in updates}
                | {r["resourceType"] for r in creates}
            ),
            "writable_refs": list(writable_refs),
            "delete_refs": [],
            "requires_read_before_write": bool(writes),
            "requires_transaction": atomic,
            "reference_calls": len(steps),
            "reference_steps": steps,
            "dependency_edges": [("supplied_mrn", "patient"), *edges],
            "gold": {
                "answer": answer,
                "evidence": evidence,
                "evidence_fields": {
                    k: sorted(set(index[k]) - {"resourceType", "id", "meta", "text"})
                    for k in evidence
                },
                "updates": changes,
                "creates": list(creates),
                "deletes": [],
            },
        }
        annotate_dependencies(task)
        tasks.append(task)

    task = index[labels["handoff-task"]]
    task_identifier = task["identifier"][0]["value"]
    reads = [
        {
            "method": "GET",
            "path": f"Task?patient={pid}&identifier={ID_SYSTEM}|{task_identifier}",
        }
    ]
    # Subsequent identities are exposed by Task.focus/input and then Episode.team.
    reads += [
        {"method": "GET", "path": labels[k]}
        for k in (
            "care-episode",
            "coordination-team",
            "transfer-list",
            "transfer-manifest",
        )
    ]
    packet = index[labels["transfer-list"]]
    reads += [
        {"method": "GET", "path": e["item"]["reference"]} for e in packet["entry"]
    ]
    evidence = [
        labels[k]
        for k in (
            "handoff-task",
            "care-episode",
            "coordination-team",
            "transfer-list",
            "transfer-manifest",
        )
    ]
    evidence += [e["item"]["reference"] for e in packet["entry"]]
    edges = [
        ("patient", "handoff"),
        ("handoff", "episode"),
        ("episode", "team"),
        ("handoff", "packet"),
        ("packet", "documents"),
    ]
    instruction = f"open transfer handoff identifier {task_identifier}, discover its care episode, team, packet list and manifest, and read each requested document. Return document_count, episode_status and receipt_documented=false. Transmission is not receipt."
    answer = {
        "document_count": len(packet["entry"]),
        "episode_status": index[labels["care-episode"]]["status"],
        "receipt_documented": False,
    }
    emit("transfer_packet_search", instruction, answer, evidence, reads, edges)
    sent = {
        "resourceType": "Communication",
        "status": "completed",
        "subject": reference(patient),
        "about": [reference(index[labels["transfer-manifest"]])],
        "sent": plan["snapshot_date"] + "T12:00:00Z",
        "payload": [
            {
                "contentString": "Requested transfer packet transmitted; receipt not yet confirmed."
            }
        ],
    }
    emit(
        "record_transfer_transmission",
        instruction
        + " The coordinator now reports that this packet was sent. Atomically set the handoff Task.status=completed and create a Communication with status=completed, subject=this patient, about=[manifest reference], sent="
        + sent["sent"]
        + ", and payload=[{contentString:'Requested transfer packet transmitted; receipt not yet confirmed.'}]. Preserve all other Task fields.",
        answer,
        evidence,
        reads,
        edges,
        updates=[(labels["handoff-task"], {"status": "completed"})],
        creates=[sent],
        atomic=True,
    )
    appt, slot, response = (
        index[labels[k]] for k in ("appointment", "slot", "appointment-response")
    )
    reads = [
        {
            "method": "GET",
            "path": f"Appointment?patient={pid}&date=eq{appt['start'][:10]}&status=booked&_include=Appointment:slot&_count=50",
        },
        {
            "method": "GET",
            "path": f"AppointmentResponse?appointment={ref(appt)}&actor={pkey}",
        },
    ]
    participants = copy.deepcopy(appt["participant"])
    next(p for p in participants if p["actor"] == reference(patient))["status"] = (
        "declined"
    )
    emit(
        "patient_declines_appointment",
        f"the verified caller declines the booked laboratory follow-up at {appt['start']}. Discover its reservation and patient response. Atomically set Appointment.status=cancelled, its patient participant.status=declined, Slot.status=free and AppointmentResponse.participantStatus=declined. Preserve other fields. Return cancelled=true and response_recorded=true.",
        {"cancelled": True, "response_recorded": True},
        [ref(appt), ref(slot), ref(response)],
        reads,
        [
            ("patient", "appointment"),
            ("appointment", "slot"),
            ("appointment", "response"),
        ],
        updates=[
            (ref(appt), {"status": "cancelled", "participant": participants}),
            (ref(slot), {"status": "free"}),
            (ref(response), {"participantStatus": "declined"}),
        ],
        atomic=True,
        writable_refs=[ref(slot)],
    )
    issue = index[labels["duplicate-review"]]
    review = {
        "resourceType": "Task",
        "status": "requested",
        "intent": "order",
        "for": reference(patient),
        "focus": reference(issue),
        "description": "Review possible duplicate laboratory requests; preserve both pending clinician review.",
    }
    reads = [
        {
            "method": "GET",
            "path": f"DetectedIssue?patient={pid}&identifier={ID_SYSTEM}|{issue['identifier'][0]['value']}",
        }
    ]
    reads += [{"method": "GET", "path": r["reference"]} for r in issue["implicated"]]
    emit(
        "review_similar_orders",
        f"a coordinator requests review of flagged item {issue['identifier'][0]['value']}. Read the implicated orders and return same_order=false and review_requested=true. Create a Task with status=requested, intent=order, for=this patient, focus=the DetectedIssue, description='{review['description']}'. Do not cancel either order.",
        {"same_order": False, "review_requested": True},
        [ref(issue), *[r["reference"] for r in issue["implicated"]]],
        reads,
        [("patient", "issue"), ("issue", "orders")],
        creates=[review],
    )
    return tasks


def structural_summary(resources):
    shapes, statuses, choices, edges = (
        defaultdict(set),
        defaultdict(set),
        Counter(),
        Counter(),
    )

    def paths(value, prefix=""):
        result = set()
        if isinstance(value, dict):
            for k, v in value.items():
                if k in {"id", "meta", "text", "note", "content", "payload"}:
                    continue
                name = prefix + "." + k if prefix else k
                result.add(name)
                result.update(paths(v, name))
        elif isinstance(value, list):
            for v in value:
                result.update(paths(v, prefix + "[]"))
        return result

    for r in resources:
        kind = r["resourceType"]
        if kind == "Provenance":
            continue
        shapes[kind].add(tuple(sorted(paths(r))))
        if "status" in r:
            statuses[kind].add(r["status"])
        if kind == "Observation":
            choices.update(
                k
                for k in r
                if k.startswith("value")
                or k in {"component", "hasMember", "dataAbsentReason"}
            )
        edges.update(
            f"{kind}->{target.split('/')[0]}"
            for target in references(r)
            if "/" in target and not target.startswith(("https:", "http:", "urn:"))
        )
    return {
        "distinct_shapes": sum(map(len, shapes.values())),
        "shapes_by_type": {k: len(v) for k, v in sorted(shapes.items())},
        "statuses_by_type": {k: sorted(v) for k, v in sorted(statuses.items())},
        "observation_structures": dict(choices),
        "reference_edge_types": len(edges),
        "reference_edges": sum(edges.values()),
        "edge_counts": dict(sorted(edges.items())),
    }


def add_narrative(r):
    """A brief faithful generated narrative; never copy the answer into text."""
    if "text" in r or r["resourceType"] == "Provenance":
        return
    parts = [r["resourceType"], "Synthetic record"]
    for field in ("status", "description", "title", "name"):
        if isinstance(r.get(field), str):
            parts.append(r[field])
    for field in ("code", "medicationCodeableConcept"):
        c = r.get(field, {})
        if isinstance(c, dict) and c.get("text"):
            parts.append(c["text"])
    # Generated text is a summary of structured fields, not clinical interpretation.
    r["text"] = {
        "status": "generated",
        "div": '<div xmlns="http://www.w3.org/1999/xhtml"><p>'
        + html.escape(". ".join(parts))
        + "</p></div>",
    }


def _replay_group(payload):
    from .cli import replay_task

    root, tasks = payload
    for task in tasks:
        replay_task(Path(root), task)
    return len(tasks)


def enhance_dataset(source_root, output, seed=17, workers=1):
    """Build in a temporary sibling and atomically publish only validated output."""
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    if not 1 <= workers <= 16:
        raise ValueError("workers must be in 1..16")
    if (
        output == source_root
        or source_root in output.parents
        or output in source_root.parents
    ):
        raise ValueError("Output must be separate from the immutable input")
    if output.exists():
        raise ValueError("Use a new output path; generated snapshots are immutable")
    old_manifest = json.loads((source_root / "manifest.json").read_text())
    before = {p: digest(source_root / p) for p in old_manifest["files"]}
    if before != old_manifest["files"]:
        raise ValueError("Input manifest checksums do not match")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".fhir-build-", dir=output.parent
    ) as work, ExitStack() as stack:
        pool = (
            stack.enter_context(
                ProcessPoolExecutor(
                    max_workers=workers, mp_context=multiprocessing.get_context("spawn")
                )
            )
            if workers > 1
            else None
        )
        root = Path(work) / "snapshot"
        root.mkdir()
        ledger = load_jsonl(source_root / "provenance-ledger.jsonl")
        all_tasks, old_resources, new_resources, repairs, plans, history, events = (
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        )
        split_counts, variant_counts = Counter(), Counter()
        replayed = 0
        for split in sorted(old_manifest["tasks"]):
            tasks = load_jsonl(source_root / split / "tasks.jsonl")
            by_shard = defaultdict(list)
            for task in tasks:
                by_shard[task["shard"]].append(copy.deepcopy(task))
            split_tasks = []
            rank = 0
            for shard, base_tasks in sorted(by_shard.items()):
                resources = load_jsonl(source_root / shard)
                old_resources.extend(copy.deepcopy(resources))
                repairs.extend(
                    {**x, "split": split, "shard": shard}
                    for x in repair_legacy(resources, base_tasks)
                )
                original_keys = {ref(r) for r in resources}
                additions = {}
                representatives = sorted(
                    (t for t in base_tasks if t["family"] == "latest_result"),
                    key=lambda t: t["patient_id"],
                )
                for t in representatives:
                    patient = next(
                        r for r in resources if ref(r) == "Patient/" + t["patient_id"]
                    )
                    plan = enhanced_plan(patient, t["snapshot_date"], rank, seed, shard)
                    compiled = EpisodeCompiler(patient, plan).compile()
                    for r in compiled["resources"]:
                        add_narrative(r)
                    variant_counts.update(
                        [plan["lab_branch"] + ":" + plan["panel_encoding"]]
                    )
                    extra_tasks = make_tasks(patient, compiled, shard)
                    for task in extra_tasks:
                        task["family"] = task["family"].replace("pilot_", "episode_")
                        task["id"] = stable_id(VERSION, patient["id"], task["family"])
                        task.update(
                            split=split,
                            upstream_split=t["upstream_split"],
                            domain="episode",
                            role="care coordinator",
                            scenario_variant=plan["lab_branch"]
                            + ":"
                            + plan["panel_encoding"],
                        )
                        task["gold"]["evidence_fields"] = {
                            k: [f for f in v if f != "text"]
                            for k, v in task["gold"]["evidence_fields"].items()
                        }
                        edges = [
                            ("supplied_mrn", "patient"),
                            ("patient", "primary"),
                            ("primary", "linked"),
                        ]
                        if "lab" in task["family"]:
                            edges = [
                                ("supplied_mrn", "patient"),
                                ("patient", "order"),
                                ("order", "report"),
                                ("order", "specimen"),
                            ]
                            result = next(
                                r
                                for r in compiled["resources"]
                                if ref(r) == compiled["labels"]["lab-report"]
                            )
                            if result.get("result"):
                                edges.append(("report", "result"))
                                if plan["panel_encoding"] == "members":
                                    edges.append(("result", "members"))
                        task["dependency_edges"] = edges
                        annotate_dependencies(task)
                    extra_tasks.extend(
                        workflow_tasks(
                            patient, compiled, shard, split, t["upstream_split"]
                        )
                    )
                    base_tasks.extend(extra_tasks)
                    for r in compiled["resources"]:
                        if ref(r) in additions and r != additions[ref(r)]:
                            raise ValueError(
                                "Shared facility definition changed across patients"
                            )
                        if ref(r) in original_keys:
                            raise ValueError(
                                "Augmentation collides with original resource"
                            )
                        if ref(r) not in additions:
                            additions[ref(r)] = r
                            ledger.append(
                                {
                                    "resource_ref": ref(r),
                                    "origin": "generated-event-graph",
                                    "source_patient_id": None
                                    if patient_ref(r) is None
                                    else patient["id"],
                                    "generator_version": VERSION,
                                    "seed": seed,
                                    "split": split,
                                    "shard": shard,
                                    "episode_id": stable_id(VERSION, patient["id"]),
                                    "source_key": None,
                                    "template": kind_template(r),
                                }
                            )
                    plans.append(
                        {
                            "split": split,
                            "shard": shard,
                            "plan": plan,
                            "labels": compiled["labels"],
                        }
                    )
                    history.extend(
                        {"split": split, "shard": shard, "resource": r}
                        for r in compiled["history"]
                    )
                    events.extend(
                        {
                            "split": split,
                            "shard": shard,
                            "patient_id": patient["id"],
                            **e,
                        }
                        for e in compiled["events"]
                    )
                    rank += 1
                deleted = {k for t in base_tasks for k in t["delete_refs"]}
                for r in list(additions.values()):
                    if ref(r) not in deleted:
                        provenance = {
                            "resourceType": "Provenance",
                            "id": stable_id(VERSION, "provenance", ref(r)),
                            "target": [reference(r)],
                            "recorded": r["meta"]["lastUpdated"],
                            "agent": [
                                {
                                    "who": {
                                        "reference": next(
                                            ref(x)
                                            for x in additions.values()
                                            if x["resourceType"] == "Organization"
                                        )
                                    }
                                }
                            ],
                            "activity": {
                                "text": "Authored synthetic event-graph fixture"
                            },
                        }
                        additions[ref(provenance)] = provenance
                resources.extend(additions.values())
                refresh_exhaustive_tasks(resources, base_tasks)
                # New records have meaningful summaries; original notes and records
                # remain byte-for-byte preserved except enumerated R4 repairs.
                for r in additions.values():
                    add_narrative(r)
                from .resource_contracts import validate_contracts as all_contracts

                all_contracts(resources)
                validate_graph(resources)
                write_jsonl(root / shard, resources)
                if pool:
                    groups = [
                        (str(root), base_tasks[i::workers]) for i in range(workers)
                    ]
                    replayed += sum(pool.map(_replay_group, groups))
                else:
                    replayed += _replay_group((str(root), base_tasks))
                new_resources.extend(resources)
                split_tasks.extend(base_tasks)
            write_jsonl(root / split / "tasks.jsonl", split_tasks)
            all_tasks.extend(split_tasks)
            split_counts[split] = len(split_tasks)
        write_jsonl(root / "provenance-ledger.jsonl", ledger)
        for name, rows in (
            ("episodes", plans),
            ("history", history),
            ("events", events),
            ("repairs", repairs),
        ):
            write_jsonl(root / "generation" / (name + ".jsonl"), rows)
        old_family = set(old_manifest["families"])
        counts = Counter(r["resourceType"] for r in new_resources)
        if not old_family <= {t["family"] for t in all_tasks} or not set(
            old_manifest["resources"]
        ) <= set(counts):
            raise ValueError("Original resource or task coverage was lost")
        if {p: digest(source_root / p) for p in before} != before:
            raise ValueError("Immutable input changed during compilation")
        summary = {
            "baseline": structural_summary(old_resources),
            "enhanced": structural_summary(new_resources),
        }
        manifest = {
            **old_manifest,
            "version": VERSION,
            "seed": seed,
            "base_manifest_sha256": digest(source_root / "manifest.json"),
            "pipeline": "source-preserving legacy coverage + standards-derived event graphs",
            "tasks": dict(split_counts),
            "families": dict(Counter(t["family"] for t in all_tasks)),
            "resources": dict(counts),
            "distinct_families": len({t["family"] for t in all_tasks}),
            "dependency_depth": dict(
                Counter(str(t["dependency_depth"]) for t in all_tasks)
            ),
            "crud": dict(
                Counter(
                    "".join(
                        x
                        for x, present in (
                            ("C", t["gold"]["creates"]),
                            ("R", True),
                            ("U", t["gold"]["updates"]),
                            ("D", t["gold"]["deletes"]),
                        )
                        if present
                    )
                    for t in all_tasks
                )
            ),
            "domains": dict(Counter(t.get("domain", "legacy") for t in all_tasks)),
            "roles": dict(Counter(t.get("role", "legacy") for t in all_tasks)),
            "variants": dict(variant_counts),
            "atomic_tasks": sum(bool(t.get("requires_transaction")) for t in all_tasks),
            "generation": {
                "additional_tasks_per_patient": 10,
                "repairs": dict(Counter(r["repair"] for r in repairs)),
                "episode_count": len(plans),
                "history_versions": len(history),
                "original_input_unchanged": True,
                "reference_tasks_replayed": replayed,
                "contracts_sha256": digest(
                    Path(__file__).with_name("resource_contracts.json")
                ),
                "generator_code_sha256": {
                    name: digest(Path(__file__).with_name(name))
                    for name in (
                        "generation.py",
                        "generation_pilot.py",
                        "dataset.py",
                        "longitudinal.py",
                        "scenarios.py",
                        "resource_contracts.py",
                        "store.py",
                        "scoring.py",
                        "validation.py",
                    )
                },
            },
            "structure": summary,
            "validation": "R4 schema + definition-derived nested cardinality/choice/reference contracts + local graph; run independent validator and reference replay before release",
            "files": {
                str(p.relative_to(root)): digest(p)
                for p in sorted(root.rglob("*.ndjson")) + sorted(root.rglob("*.jsonl"))
            },
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        root.rename(output)
    return manifest


def kind_template(resource):
    return resource["resourceType"] + ":event-graph"


def refresh_exhaustive_tasks(resources, tasks):
    """Recompute exhaustive gold sets after adding records, keeping task intent."""
    from .store import FhirStore

    index = {ref(r): r for r in resources}
    for task in tasks:
        # Old plans relied on a default ten-record page. Denser charts require
        # an explicit bounded page and the date already supplied in the request.
        for step in task["reference_steps"]:
            if (
                step["method"] == "GET"
                and "?" in step["path"]
                and "_count=" not in step["path"]
            ):
                step["path"] += "&_count=50"
        if task["family"] == "message_followup":
            message = next(
                index[k]
                for k in task["gold"]["evidence"]
                if k.startswith("Communication/")
            )
            for step in task["reference_steps"]:
                if step["path"].startswith("Communication?"):
                    step["path"] += "&sent=eq" + message["sent"][:10]
        if task["family"] in {"documented_home_medications", "documented_conditions"}:
            tag = ORIGIN_SYSTEM + "|source-profile"
            for step in task["reference_steps"]:
                if step["path"].startswith(("MedicationStatement?", "Condition?")):
                    step["path"] += "&_tag=" + tag
            if task.get("absence_scope"):
                task["absence_scope"]["tag"] = tag
            task["prompt"] += "\nImport scope: meta.tag=" + tag + "."
        if task["family"] == "longitudinal_lab_trend":
            store = FhirStore(resources, "Patient/" + task["patient_id"])
            query = next(
                s["path"]
                for s in task["reference_steps"]
                if s["path"].startswith("Observation?")
            )
            result = store.request("GET", query)
            if result["status"] != 200:
                raise ValueError("Exhaustive task refresh failed")
            bundle = result["body"]
            records = [
                e["resource"] for e in bundle["entry"] if e["search"]["mode"] == "match"
            ]
            if len(records) != bundle["total"]:
                raise ValueError("Exhaustive reference query requires pagination")
            task["gold"]["answer"] = {
                "results": [
                    {
                        "date": r["effectiveDateTime"][:10],
                        "value": r["valueQuantity"]["value"],
                        "unit": r["valueQuantity"]["unit"],
                    }
                    for r in records
                ]
            }
            patient = "Patient/" + task["patient_id"]
            task["gold"]["evidence"] = [patient, *[ref(r) for r in records]]
            task["gold"]["target_evidence"] = [ref(r) for r in records]
            task["gold"]["evidence_fields"] = {
                ref(r): ["effectiveDateTime", "valueQuantity", "code", "status"]
                for r in records
            }
            if patient in index:
                task["gold"]["evidence_fields"][patient] = ["identifier"]


def build_enhanced_dataset(output, source_db=None, seed=17, shard_size=8, workers=1):
    with tempfile.TemporaryDirectory(prefix="fhir-baseline-") as work:
        base = Path(work) / "baseline"
        build_dataset(base, source_db, seed, shard_size)
        return enhance_dataset(base, output, seed, workers=workers)
