#!/usr/bin/env python3
"""Summarize a compiled benchmark and export a dev-only human review pack.

This reads the release manifest, every task and provenance-ledger row, and only
the dev shards needed for the examples. It does not run a model or training.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any


EXAMPLE_FAMILIES = (
    "nursing_observation_and_visit_close",
    "reschedule_with_slot_release",
    "specimen_recollection_request",
    "outside_immunization_documentation",
    "duplicate_charge_correction",
    "delete_unsubmitted_duplicate_claim",
    "records_release_scope_review",
    "medication_supply_vs_administration",
    "home_equipment_handoff",
    "nutrition_order_discrepancy",
    "referral_loop_closure",
    "reported_allergy_with_uncertainty",
)

PREFERRED_VARIANTS = {
    "nursing_observation_and_visit_close": "pediatric_caregiver",
    "outside_immunization_documentation": "patient_report_date_unknown",
    "records_release_scope_review": "scope_expired",
    "home_equipment_handoff": "delivered_training_pending",
    "referral_loop_closure": "delivery_not_acknowledged",
}


def rows(path: Path):
    with path.open() as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {path}:{number}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution(values) -> dict[str, int | float]:
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": values[0],
        "p25": values[round((len(values) - 1) * 0.25)],
        "median": statistics.median(values),
        "p75": values[round((len(values) - 1) * 0.75)],
        "max": values[-1],
        "mean": round(statistics.mean(values), 3),
    }


def sorted_counts(counts) -> dict:
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))


def crud(task: dict) -> str:
    gold = task["gold"]
    return "".join(
        action
        for action, present in (
            ("C", gold.get("creates")),
            ("R", True),
            ("U", gold.get("updates")),
            ("D", gold.get("deletes")),
        )
        if present
    )


def count_table(counts: dict, label: str = "Category") -> str:
    lines = [f"| {label} | Count |", "|---|---:|"]
    lines.extend(f"| {key} | {value:,} |" for key, value in counts.items())
    return "\n".join(lines)


def pretty(value) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def origin_of(resource: dict, ledger: dict) -> str:
    reference = resource["resourceType"] + "/" + resource["id"]
    return ledger.get(reference, {}).get("origin", "not present in ledger")


def generated_text(resource: dict) -> list[str]:
    """Only extract text from an already-selected generated evidence resource."""
    texts = []
    if resource["resourceType"] == "DocumentReference":
        for content in resource.get("content", []):
            attachment = content.get("attachment", {})
            if attachment.get("contentType", "").startswith("text/") and attachment.get("data"):
                texts.append(base64.b64decode(attachment["data"], validate=True).decode("utf-8"))
    for payload in resource.get("payload", []):
        if "contentString" in payload:
            texts.append(payload["contentString"])
    for annotation in resource.get("note", []):
        if annotation.get("text"):
            texts.append(annotation["text"])
    return texts


def evidence_excerpt(resource: dict) -> dict:
    """Keep clinically relevant fields but omit bulky notes and bookkeeping."""
    omit = {"id", "meta", "identifier", "text", "content", "payload", "note"}
    return {key: value for key, value in resource.items() if key not in omit}


def reference_summary(task: dict) -> list[str]:
    result = []
    for number, step in enumerate(task["reference_steps"], 1):
        line = f"{number}. `{step['method']} /{step['path']}`"
        body = step.get("body", {})
        if body.get("resourceType") == "Bundle" and body.get("type") == "transaction":
            parts = [
                f"{entry['request']['method']} {entry['request']['url']}"
                for entry in body["entry"]
            ]
            line += ": atomic transaction containing " + "; ".join(f"`{part}`" for part in parts)
        if step.get("headers"):
            line += " with " + json.dumps(step["headers"], sort_keys=True)
        result.append(line)
    return result


def review_pack(root: Path, examples: list[dict], ledger: dict) -> str:
    lines = [
        "# Clinical and clerical review pack",
        "",
        "These 12 examples are selected only from the dev split. They are synthetic, "
        "template-generated workflow cases and have not been clinician-validated. "
        "The supplied answers and requests are benchmark reference workflows, not model "
        "outputs or measures of model performance. They are intended for review of "
        "clinical plausibility, clarity, scope, missing information, and state changes.",
        "",
        "Source Hospital profile facts and preserved notes remain distinct from generated "
        "workflow events. Birth dates and names are generated demographics; the age below "
        "is calculated from that birth date at the case snapshot, not a separately verified "
        "source age. Source clinical note bodies are not reproduced in this pack. "
        "FHIR references identify synthetic sandbox records only.",
        "",
        "The new tasks provide explicit serialization contracts to make exact state "
        "verification possible. Reviewers should assess whether each narrative supports "
        "the requested changes; success on these constrained cases does not establish "
        "performance on unconstrained EHR requests or clinical decision making.",
        "",
    ]
    cache = {}
    for number, task in enumerate(examples, 1):
        if task["split"] != "dev" or not task["shard"].startswith("dev/"):
            raise ValueError("Review examples must belong to the dev split")
        if task["shard"] not in cache:
            cache[task["shard"]] = {
                r["resourceType"] + "/" + r["id"]: r for r in rows(root / task["shard"])
            }
        resources = cache[task["shard"]]
        patient = resources["Patient/" + task["patient_id"]]
        birth = date.fromisoformat(patient["birthDate"])
        snapshot = date.fromisoformat(task["snapshot_date"])
        age = snapshot.year - birth.year - ((snapshot.month, snapshot.day) < (birth.month, birth.day))
        patient_ledger = ledger["Patient/" + task["patient_id"]]
        lines.extend([
            f"## {number}. {task['family'].replace('_', ' ').capitalize()}",
            "",
            f"Task `{task['id']}` · {task['domain']} · {task['role']} · CRUD `{crud(task)}` "
            f"· variant `{task.get('scenario_variant', 'baseline')}`",
            "",
            f"Synthetic patient `{task['patient_id']}`; source patient key "
            f"`{patient_ledger['source_patient_id']}`. Generated birth date "
            f"{patient['birthDate']}; age {age} at {task['snapshot_date']}. "
            f"Patient origin: `{patient_ledger['origin']}`. "
            f"Template dependency depth {task['dependency_depth']}; workflow depth "
            f"{task['workflow_depth']}; {task['reference_calls']} reference API calls. "
            "These depths are declared prerequisite graphs, not proven minimum-call plans.",
            "",
            "### User request",
            "",
            *["> " + line for line in task["prompt"].splitlines()],
            "",
            "### Relevant generated notes and evidence",
            "",
        ])
        note_count = 0
        for reference in task["gold"]["evidence"]:
            resource = resources.get(reference)
            if resource is None:
                lines.extend([f"- `{reference}`: absent in the sampled snapshot.", ""])
                continue
            origin = origin_of(resource, ledger)
            if origin.startswith("generated"):
                for text in generated_text(resource):
                    note_count += 1
                    excerpt = text[:1600]
                    if len(text) > 1600:
                        excerpt += " [excerpt truncated]"
                    lines.extend([
                        f"**`{reference}` — `{origin}`**",
                        "",
                        *["> " + line for line in excerpt.splitlines()],
                        "",
                    ])
        if not note_count:
            lines.extend([
                "This case is grounded in the structured resources below; its supporting "
                "evidence contains no generated narrative note. No narrative has been invented "
                "for the review pack.",
                "",
            ])
        for reference in task["gold"]["evidence"]:
            resource = resources.get(reference)
            if resource and resource["resourceType"] not in {"Patient", "DocumentReference"}:
                lines.extend([
                    f"**`{reference}` — `{origin_of(resource, ledger)}`**",
                    "",
                    pretty(evidence_excerpt(resource)),
                    "",
                ])
        lines.extend([
            "### Expected answer and permitted changes",
            "",
            pretty({"answer": task["gold"]["answer"], "evidence": task["gold"]["evidence"]}),
            "",
            pretty({
                "creates": task["gold"].get("creates", []),
                "updates": task["gold"].get("updates", []),
                "deletes": task["gold"].get("deletes", []),
                "requires_read_before_write": task.get("requires_read_before_write", False),
                "requires_transaction": task.get("requires_transaction", False),
            }),
            "",
            "All other state must remain unchanged. Listed creates describe expected resource "
            "bodies; the server assigns resource IDs. An allowed type alone does not authorize "
            "unrelated changes.",
            "",
            "### Reference request sequence",
            "",
            *reference_summary(task),
            "",
            "### Reviewer checks",
            "",
            "Is the instruction appropriate for the named role? Does every factual claim have "
            "support? Are uncertain reports preserved as uncertain? Are the permitted changes "
            "sufficient, and is any important workflow step missing? Would an alternative correct "
            "sequence or resource representation be unfairly rejected?",
            "",
        ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root, output = args.dataset.resolve(), args.output.resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    output.mkdir(parents=True, exist_ok=True)

    counters = {key: Counter() for key in (
        "families", "domains", "roles", "crud", "variants", "dependency_depth", "workflow_depth",
        "reference_calls", "age_band", "information_regime",
    )}
    per_split = {}
    per_family: dict[str, dict[str, Any]] = {}
    candidates = {}
    task_patients = defaultdict(set)
    task_counts_by_patient = Counter()
    domains_by_patient = defaultdict(set)
    atomic_tasks = 0
    read_before_write_tasks = 0
    omitted_resource_tasks = 0
    checked_hashes = {}
    for split in sorted(manifest["tasks"]):
        path = root / split / "tasks.jsonl"
        relative = path.relative_to(root).as_posix()
        digest = sha256(path)
        if digest != manifest["files"][relative]:
            raise ValueError(f"Manifest digest mismatch: {relative}")
        checked_hashes[relative] = digest
        count = 0
        for task in rows(path):
            if task["split"] != split:
                raise ValueError(f"Task split mismatch: {task['id']}")
            count += 1
            family, domain = task["family"], task.get("domain", "legacy")
            role, variant = task.get("role", "legacy"), task.get("scenario_variant", "baseline")
            for key, value in (
                ("families", family), ("domains", domain), ("roles", role),
                ("crud", crud(task)), ("variants", f"{domain}:{variant}"),
                ("dependency_depth", str(task["dependency_depth"])),
                ("workflow_depth", str(task["workflow_depth"])),
                ("reference_calls", str(task["reference_calls"])),
                ("age_band", task.get("age_band", "not_annotated")),
                ("information_regime", task.get("information_regime", "not_annotated")),
            ):
                counters[key][value] += 1
            atomic_tasks += bool(task.get("requires_transaction"))
            read_before_write_tasks += bool(task.get("requires_read_before_write"))
            omitted_resource_tasks += bool(task.get("omit"))
            task_patients[split].add(task["patient_id"])
            task_counts_by_patient[task["patient_id"]] += 1
            if domain != "legacy":
                domains_by_patient[task["patient_id"]].add(domain)
            detail = per_family.setdefault(family, {
                "tasks": 0,
                **{key: Counter() for key in (
                    "domains", "roles", "crud", "splits", "variants", "dependency_depths",
                    "workflow_depths", "reference_calls", "age_bands", "information_regimes",
                    "requires_transaction", "requires_read_before_write",
                )},
            })
            detail["tasks"] += 1
            for key, value in (
                ("domains", domain), ("roles", role), ("crud", crud(task)),
                ("splits", split), ("variants", variant),
                ("dependency_depths", str(task["dependency_depth"])),
                ("workflow_depths", str(task["workflow_depth"])),
                ("reference_calls", str(task["reference_calls"])),
                ("age_bands", task.get("age_band", "not_annotated")),
                ("information_regimes", task.get("information_regime", "not_annotated")),
                ("requires_transaction", str(bool(task.get("requires_transaction"))).lower()),
                ("requires_read_before_write", str(bool(task.get("requires_read_before_write"))).lower()),
            ):
                detail[key][value] += 1
            if split == "dev" and family in EXAMPLE_FAMILIES:
                score = int(variant == PREFERRED_VARIANTS.get(family))
                if family not in candidates or score > candidates[family][0]:
                    candidates[family] = (score, task)
        per_split[split] = {"patients": len(task_patients[split]), "tasks": count}
        if count != manifest["tasks"][split] or len(task_patients[split]) != manifest["patients"][split]:
            raise ValueError(f"Manifest split count mismatch: {split}")

    ledger_path = root / "provenance-ledger.jsonl"
    ledger_hash = sha256(ledger_path)
    if ledger_hash != manifest["files"]["provenance-ledger.jsonl"]:
        raise ValueError("Manifest digest mismatch: provenance-ledger.jsonl")
    checked_hashes["provenance-ledger.jsonl"] = ledger_hash
    origins, resource_counts = Counter(), Counter()
    per_patient_resources, per_patient_types = Counter(), defaultdict(set)
    per_patient_origins = defaultdict(Counter)
    dev_ledger = {}
    for entry in rows(ledger_path):
        kind = entry["resource_ref"].split("/", 1)[0]
        if kind == "Provenance":
            raise ValueError("The breadth ledger must exclude Provenance")
        key = str(entry["source_patient_id"])
        origins[entry["origin"]] += 1
        resource_counts[kind] += 1
        per_patient_resources[key] += 1
        per_patient_types[key].add(kind)
        per_patient_origins[key][entry["origin"]] += 1
        if entry["split"] == "dev":
            dev_ledger[entry["resource_ref"]] = entry
    expected_resources = {kind: n for kind, n in manifest["resources"].items() if kind != "Provenance"}
    if dict(resource_counts) != expected_resources:
        raise ValueError("Ledger resource counts do not match the manifest")
    if len(per_patient_resources) != sum(manifest["patients"].values()):
        raise ValueError("Ledger patient counts do not match the manifest")
    for key in ("families", "domains", "roles", "crud", "variants", "dependency_depth"):
        if dict(counters[key]) != manifest[key]:
            raise ValueError(f"Task aggregate does not match manifest: {key}")
    if atomic_tasks != manifest["atomic_tasks"]:
        raise ValueError("Atomic task aggregate does not match manifest")
    if len(candidates) != len(EXAMPLE_FAMILIES):
        raise ValueError("A required dev review family is missing")
    for family, detail in per_family.items():
        for key, counts in detail.items():
            if key != "tasks" and sum(counts.values()) != detail["tasks"]:
                raise ValueError(f"Per-family aggregate mismatch: {family}/{key}")
        per_family[family] = {
            key: sorted_counts(value) if isinstance(value, Counter) else value
            for key, value in detail.items()
        }

    examples = [candidates[family][1] for family in EXAMPLE_FAMILIES]
    coverage = {
        "version": manifest["version"],
        "dataset": str(root),
        "manifest_sha256": sha256(manifest_path),
        "source": manifest["source"],
        "source_sha256": manifest["source_sha256"],
        "seed": manifest["seed"],
        "totals": {
            "patients": sum(manifest["patients"].values()),
            "tasks": sum(manifest["tasks"].values()),
            "distinct_families": len(counters["families"]),
            "resources_including_provenance": sum(manifest["resources"].values()),
            "resources_excluding_provenance": sum(resource_counts.values()),
            "provenance_resources": manifest["resources"].get("Provenance", 0),
            "resource_types_including_provenance": len(manifest["resources"]),
            "resource_types_excluding_provenance": len(resource_counts),
            "atomic_tasks": atomic_tasks,
            "read_before_write_tasks": read_before_write_tasks,
            "tasks_with_omitted_resources": omitted_resource_tasks,
        },
        "splits": per_split,
        "task_aggregates": {key: sorted_counts(value) for key, value in counters.items()},
        "families": dict(sorted(per_family.items())),
        "resources": manifest["resources"],
        "ledger_origins": sorted_counts(origins),
        "per_patient": {
            "ledger_resources_excluding_provenance": distribution(per_patient_resources.values()),
            "ledger_resource_types_excluding_provenance": distribution(map(len, per_patient_types.values())),
            "task_instances": distribution(task_counts_by_patient.values()),
            "annotated_workflow_domains_excluding_legacy": distribution(map(len, domains_by_patient.values())),
            "resource_count_histogram": sorted_counts(Counter(map(str, per_patient_resources.values()))),
            "resource_type_count_histogram": sorted_counts(Counter(str(len(v)) for v in per_patient_types.values())),
        },
        "review_examples": [
            {"task_id": t["id"], "family": t["family"], "split": "dev", "shard": t["shard"]}
            for t in examples
        ],
        "verification": {
            "task_and_ledger_digests_match_manifest": True,
            "task_aggregates_match_manifest": True,
            "ledger_non_provenance_resource_counts_match_manifest": True,
            "checked_file_sha256": checked_hashes,
            "scope": "Counts all task and ledger rows; samples dev shards for review. Provenance counts "
            "come from the manifest. Does not revalidate all shard contents or execute reference workflows.",
            "generator_validation_claim": manifest["validation"],
        },
        "limitations": [
            "Synthetic templates; no clinician validation or representative health-system prevalence claim.",
            "Task instances and variants are not independent workflow families or independent clinical cases.",
            "Legacy marks tasks without new domain/role metadata, not a clinical domain or job role.",
            "CRUD R is included for every task; C/U/D derive from the expected state delta.",
            "Dependency depth is template-declared, not a proven minimum call count.",
            "Ledger breadth attributes support resources to the source patient used to generate them; "
            "this includes organizations and schedules, not only resources with a direct Patient subject.",
            "FHIR JSON schema and local integrity checks do not establish full FHIRPath/profile, "
            "terminology, independent-server, or clinical conformance.",
            "Reference request sequences and expected answers are not model performance results.",
        ],
    }
    (output / "coverage.json").write_text(json.dumps(coverage, indent=2, ensure_ascii=False) + "\n")
    (output / "example-tasks.dev.jsonl").write_text(
        "".join(json.dumps(task, sort_keys=True, ensure_ascii=False) + "\n" for task in examples)
    )
    (output / "clinical-review-pack.md").write_text(review_pack(root, examples, dev_ledger))
    totals = coverage["totals"]
    breadth = coverage["per_patient"]
    report = [
        f"# FHIR workflows {manifest['version']} coverage",
        "",
        f"The compiled snapshot contains **{totals['patients']:,} patients**, "
        f"**{totals['tasks']:,} task instances across {totals['distinct_families']} families**, "
        f"and **{totals['resource_types_including_provenance']} FHIR resource types**. "
        f"There are {totals['resources_excluding_provenance']:,} non-Provenance resources "
        f"and {totals['provenance_resources']:,} Provenance resources "
        f"({totals['resources_including_provenance']:,} total).",
        "",
        "These are generated benchmark counts, not evidence of clinical realism or model success. "
        "The corpus combines preserved Synthetic Hospital source material with explicitly labelled "
        "synthetic workflow extensions. No clinician validation is claimed.",
        "",
        "## Partitions",
        "",
        "| Split | Patients | Task instances |", "|---|---:|---:|",
        *[f"| {split} | {v['patients']:,} | {v['tasks']:,} |" for split, v in per_split.items()],
        "",
        "Only dev examples appear in the review pack and example JSONL. Aggregate counts include "
        "all partitions. Task counts do not imply that repeated template instantiations are "
        "independent workflow designs.",
        "",
        "## Breadth per patient",
        "",
        "| Measure | Min | Median | Mean | Max |", "|---|---:|---:|---:|---:|",
        *[f"| {label} | {breadth[key]['min']} | {breadth[key]['median']} | "
          f"{breadth[key]['mean']} | {breadth[key]['max']} |" for key, label in (
            ("ledger_resources_excluding_provenance", "Resources, excluding Provenance"),
            ("ledger_resource_types_excluding_provenance", "Resource types, excluding Provenance"),
            ("task_instances", "Task instances"),
            ("annotated_workflow_domains_excluding_legacy", "Annotated workflow domains"),
        )],
        "",
        "Ledger attribution includes the supporting organizations, practitioners, schedules and "
        "slots generated with a patient. It does not mean every counted resource has a direct "
        "Patient subject. Supporting provenance is excluded to avoid inflating chart breadth.",
        "",
        "## CRUD and workflow complexity",
        "",
        count_table(coverage["task_aggregates"]["crud"], "Expected operations"),
        "",
        f"{atomic_tasks:,} tasks explicitly require an atomic transaction; "
        f"{read_before_write_tasks:,} require reads before mutation; "
        f"{omitted_resource_tasks:,} have episode resources deliberately omitted. "
        "All tasks include retrieval; create/update/delete labels come from their expected "
        "state deltas. A correction marked entered-in-error is an update, not a delete.",
        "",
        count_table(coverage["task_aggregates"]["dependency_depth"], "Declared dependency depth"),
        "",
        "Depth describes template prerequisites, not a proved optimal number of calls. The "
        "machine-readable report separately records workflow depth and reference-call counts.",
        "",
        "## Workflow domains", "", count_table(coverage["task_aggregates"]["domains"], "Domain"),
        "",
        "`legacy` means the original tasks lack the newer domain annotation; it is not a "
        "clinical category. Those families remain individually visible below.",
        "",
        "## Roles", "", count_table(coverage["task_aggregates"]["roles"], "Role"),
        "",
        "## Families", "",
        "| Family | Domain | CRUD | Instances |", "|---|---|---|---:|",
        *[f"| {family} | {', '.join(details['domains'])} | {', '.join(details['crud'])} | {details['tasks']:,} |"
          for family, details in sorted(per_family.items())],
        "",
        "Each family row lists every observed CRUD combination and domain across its instances. "
        "A family may require a read-only answer in one branch and an update in another. "
        "The machine-readable report preserves per-family counts for CRUD, roles, domains, "
        "variants, depths, reference calls, age bands, and transaction/read-before-write constraints.",
        "",
        "## Scenario variants", "", count_table(coverage["task_aggregates"]["variants"], "Domain:variant"),
        "",
        "Variant counts are task instances. Multiple tasks may operate on the same patient "
        "episode and therefore share one scenario variant.",
        "",
        "## Resource inventory", "", count_table(manifest["resources"], "FHIR R4 resource type"),
        "",
        "## Source and generated material", "", count_table(coverage["ledger_origins"], "Ledger origin"),
        "",
        "Preserved source records and extracted profile facts are distinguished from generated "
        "demographics, providers, longitudinal context and workflow modules by ledger origin. "
        "Generated events are not assertions that those events happened in the source dataset.",
        "",
        "## Verification and limits", "",
        "This script verifies task and ledger hashes against the manifest, reconciles split "
        "patient/task counts, reconciles all non-Provenance resource counts, and checks the "
        "family/domain/role/CRUD/variant/depth aggregates. It samples dev shards only for the "
        "review pack. It does not rerun reference workflows or validate every shard.",
        "",
        f"Generator validation statement: {manifest['validation']}.",
        "",
        *[f"- {limit}" for limit in coverage["limitations"]],
        "",
        "## Files and reproduction", "",
        "- [Machine-readable coverage](coverage.json)",
        "- [12-case clinical and clerical review pack](clinical-review-pack.md)",
        "- [Exact dev example tasks](example-tasks.dev.jsonl)",
        "",
        "```sh",
        ".venv/bin/python scripts/benchmark_report.py data/synthetic-hospital-v0.3.0 "
        "--output artifacts/benchmark-v0.3.0",
        "```",
        "",
        f"Manifest SHA-256: `{coverage['manifest_sha256']}`.",
        "",
    ]
    (output / "README.md").write_text("\n".join(report))
    print(json.dumps({"output": str(output), "totals": totals, "review_examples": len(examples)}, indent=2))


if __name__ == "__main__":
    main()
