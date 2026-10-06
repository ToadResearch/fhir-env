#!/usr/bin/env python3
"""Build an isolated dev-only generation comparison and executable review pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from fhir_query_rl.cli import replay_task
from fhir_query_rl.dataset import ORIGIN_SYSTEM, load_jsonl, write_jsonl
from fhir_query_rl.generation_pilot import (
    CATALOG_PATH,
    PILOT_VERSION,
    compile_episode,
    make_tasks,
    plan_episode,
)
from fhir_query_rl.store import FhirStore
from fhir_query_rl.validation import patient_ref, ref, references, validate_graph

COMMON_TYPES = (
    "Observation",
    "DiagnosticReport",
    "ServiceRequest",
    "Specimen",
    "MedicationRequest",
    "MedicationDispense",
    "Appointment",
    "Slot",
    "Coverage",
    "CoverageEligibilityResponse",
    "Communication",
    "Task",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generated(r):
    return any(
        t.get("system") == ORIGIN_SYSTEM and t.get("code", "").startswith("generated-")
        for t in r.get("meta", {}).get("tag", [])
    )


def shape_paths(value, prefix=""):
    """Ignore IDs, metadata, narrative bodies and values; retain FHIR field shape."""
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"id", "meta", "text", "note", "content", "payload"}:
                continue
            path = prefix + "." + key if prefix else key
            paths.add(path)
            paths |= shape_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            paths |= shape_paths(child, prefix + "[]")
    return paths


def features(r):
    f = set()
    if r["resourceType"] == "Observation":
        for field in (
            "component",
            "hasMember",
            "dataAbsentReason",
            "valueQuantity",
            "valueCodeableConcept",
            "effectivePeriod",
        ):
            if field in r:
                f.add("Observation." + field)
        if r.get("status") == "corrected":
            f.add("Observation.corrected")
    if len(r.get("identifier", [])) > 1:
        f.add("multiple_identifiers")
    if r["resourceType"] in {"MedicationRequest", "MedicationDispense"}:
        for field in ("medicationReference", "medicationCodeableConcept"):
            if field in r:
                f.add(r["resourceType"] + "." + field)
    for field in (
        "basedOn",
        "encounter",
        "specimen",
        "result",
        "authorizingPrescription",
    ):
        if field in r:
            f.add(r["resourceType"] + "." + field)
    if r["resourceType"] == "DiagnosticReport" and r.get("status") in {
        "registered",
        "cancelled",
        "corrected",
    }:
        f.add("DiagnosticReport." + r["status"])
    if r["resourceType"] == "MedicationDispense":
        if r.get("whenPrepared") and not r.get("whenHandedOver"):
            f.add("MedicationDispense.prepared_without_handover")
        if r.get("whenHandedOver"):
            f.add("MedicationDispense.handed_over")
    if r["resourceType"] == "CoverageEligibilityResponse":
        for insurance in r.get("insurance", []):
            f.add("Eligibility.inforce_" + str(insurance.get("inforce")).lower())
            f.add(
                "Eligibility.benefitPeriod_" + str("benefitPeriod" in insurance).lower()
            )
    return f


def describe(resources):
    resources = [r for r in resources if r["resourceType"] in COMMON_TYPES]
    shapes, statuses, counts, feature_counts, edges = (
        defaultdict(set),
        defaultdict(set),
        Counter(),
        Counter(),
        Counter(),
    )
    for r in resources:
        kind = r["resourceType"]
        counts[kind] += 1
        shapes[kind].add(tuple(sorted(shape_paths(r))))
        statuses[kind].add(r.get("status", "(none)"))
        feature_counts.update(features(r))
        edges.update(
            kind + " -> " + target.split("/")[0]
            for target in references(r)
            if not target.startswith(("https:", "http:", "#", "urn:"))
            and not target.startswith("Patient/")
        )
    return {
        "resources": len(resources),
        "counts": dict(sorted(counts.items())),
        "shapes_by_type": {k: len(shapes[k]) for k in sorted(shapes)},
        "distinct_shapes": sum(map(len, shapes.values())),
        "statuses_by_type": {k: sorted(statuses[k]) for k in sorted(statuses)},
        "features": dict(sorted(feature_counts.items())),
        "distinct_features": len(feature_counts),
        "nonpatient_reference_edges": sum(edges.values()),
        "edge_types": dict(sorted(edges.items())),
    }


def balanced_comparison(old, new, seed):
    pools = [
        {k: [r for r in records if r["resourceType"] == k] for k in COMMON_TYPES}
        for records in (old, new)
    ]
    budgets = {k: min(len(pools[0][k]), len(pools[1][k])) for k in COMMON_TYPES}
    metrics = [defaultdict(list), defaultdict(list)]
    for iteration in range(100):
        rng = random.Random(seed + iteration)
        for side in (0, 1):
            sample = [
                r
                for k, budget in budgets.items()
                for r in rng.sample(pools[side][k], budget)
            ]
            d = describe(sample)
            for key in (
                "distinct_shapes",
                "distinct_features",
                "nonpatient_reference_edges",
            ):
                metrics[side][key].append(d[key])
    return {
        "method": "100 deterministic resamples; equal resource counts per type, common 12 types, same patients; no significance claim",
        "counts_per_type": budgets,
        "records_per_side": sum(budgets.values()),
        **{
            side: {
                k: {"mean": round(statistics.mean(v), 2), "min": min(v), "max": max(v)}
                for k, v in metrics[i].items()
            }
            for i, side in enumerate(("old", "pilot"))
        },
    }


def choose_cohort(root, tasks):
    # latest_result exists for every source patient and has a valid source snapshot.
    representatives = {
        t["patient_id"]: t for t in tasks if t["family"] == "latest_result"
    }
    shards = {
        shard: load_jsonl(root / shard)
        for shard in sorted({t["shard"] for t in representatives.values()})
    }
    strata = defaultdict(list)
    for pid, t in sorted(representatives.items()):
        patient = next(
            r
            for r in shards[t["shard"]]
            if r["resourceType"] == "Patient" and r["id"] == pid
        )
        at, birth = (
            date.fromisoformat(t["snapshot_date"]),
            date.fromisoformat(patient["birthDate"]),
        )
        age = at.year - birth.year - ((at.month, at.day) < (birth.month, birth.day))
        band = (
            "child"
            if age < 18
            else "adult18_39"
            if age < 40
            else "adult40_64"
            if age < 65
            else "older65plus"
        )
        strata[band].append((patient, t, age))
    # Interleave age bands so balanced branch assignment is not confounded with age.
    bands = ("child", "adult18_39", "adult40_64", "older65plus")
    if any(len(strata[b]) < 6 for b in bands):
        raise ValueError("Need at least six dev patients in each of the four age bands")
    cohort = [(*strata[b][i], b) for i in range(6) for b in bands]
    return cohort, shards


def closure(resources, patient):
    index = {ref(r): r for r in resources}
    patient_key = ref(patient)
    keys = {k for k, r in index.items() if patient_ref(r) == patient_key}
    pending = list(keys)
    while pending:
        key = pending.pop()
        for target in references(index[key]):
            if target in index and target not in keys:
                keys.add(target)
                pending.append(target)
    return [index[k] for k in sorted(keys)]


def probe_queries(compiled, patient, baseline):
    """Ground truth from chart traversal; reference requests exercise current API."""
    store = FhirStore([*baseline, *compiled["resources"]])
    index = store.resources
    labels = compiled["labels"]
    paths = [
        (
            "report_order_and_results",
            f"DiagnosticReport?patient={patient['id']}&based-on={labels['lab-order']}&_include=DiagnosticReport:result",
        ),
        (
            "appointment_slot",
            f"Appointment?_id={labels['appointment'].split('/')[1]}&_include=Appointment:slot",
        ),
        (
            "eligibility_request",
            f"CoverageEligibilityResponse?_id={labels['eligibility-response'].split('/')[1]}&_include=CoverageEligibilityResponse:request",
        ),
        (
            "medication_code",
            f"MedicationRequest?patient={patient['id']}&code=https://fhir-workflows.example/codes|"
            + (
                "acetaminophen-tablet"
                if compiled["plan"]["age"] >= 18
                else "acetaminophen-solution"
            ),
        ),
    ]
    out = []
    expected_matches = [
        labels[k]
        for k in ("lab-report", "appointment", "eligibility-response", "prescription")
    ]
    for (name, path), expected in zip(paths, expected_matches):
        result = store.request("GET", path)
        matched = [
            ref(e["resource"])
            for e in result.get("body", {}).get("entry", [])
            if e.get("search", {}).get("mode") == "match"
        ]
        if result["status"] != 200 or matched != [expected]:
            raise ValueError(f"Query mismatch {name}: {result}")
        out.append(
            {"name": name, "path": path, "matches": matched, "status": result["status"]}
        )
    if "lab-panel" in labels and index[labels["lab-panel"]].get("hasMember"):
        out.append(
            {
                "name": "panel_member_access",
                "environment_support": False,
                "note": "Observation hasMember resources require direct reads; the current subset has no Observation:has-member include.",
            }
        )
    return out


def audit_discovery(task, resources):
    """Reference reads may use only prompt clues or identifiers already exposed."""
    store = FhirStore(resources)
    visible = task["prompt"]
    for step in task["reference_steps"]:
        if step["method"] != "GET":
            break
        path = step["path"]
        for identity in re.findall(r"(?<![a-f0-9])[a-f0-9]{24}(?![a-f0-9])", path):
            if identity not in visible:
                raise ValueError(
                    "Reference plan used an undiscovered FHIR ID: " + identity
                )
        for key, value in parse_qsl(urlsplit(path).query):
            if key == "identifier" and value.split("|")[-1] not in visible:
                raise ValueError(
                    "Reference plan used an undiscovered business identifier"
                )
        response = store.request("GET", path)
        if response["status"] != 200:
            raise ValueError("Reference discovery query failed")
        visible += json.dumps(response["body"])


def write_report(output, summary):
    old, new, balanced = (
        summary["old_common_types"],
        summary["pilot_common_types"],
        summary["balanced"],
    )
    lines = [
        "# Generation pilot: measured comparison",
        "",
        "24 existing development patients; six per age band. The frozen corpus is unchanged.",
        "",
        "The control is generated resources from their existing charts. The candidate adds five event-based episode domains to those same patients. This is not a Synthea or LLM generation comparison.",
        "",
        "## Common resource types",
        "",
        "| Type | Old records | Pilot additions | Old shapes | Pilot shapes |",
        "|---|---:|---:|---:|---:|",
    ]
    for kind in COMMON_TYPES:
        lines.append(
            f"| {kind} | {old['counts'].get(kind, 0)} | {new['counts'].get(kind, 0)} | {old['shapes_by_type'].get(kind, 0)} | {new['shapes_by_type'].get(kind, 0)} |"
        )
    lines += [
        "",
        "Shapes are unique sets of JSON field paths; IDs, metadata and narrative bodies are excluded. This measures structural variety, not clinical quality.",
        "",
        f"The full old generated cohort covers {summary['old_generated_types']} types; the narrower pilot covers {summary['pilot_generated_types']}. A higher type count is not a pilot result.",
        "",
        "## Equal record budgets",
        "",
        f"Each side contributes {balanced['records_per_side']} records, with identical counts per common resource type. Results below are means over 100 deterministic subsamples.",
        "",
        "| Metric | Old | Pilot |",
        "|---|---:|---:|",
    ]
    for key in ("distinct_shapes", "distinct_features", "nonpatient_reference_edges"):
        lines.append(
            f"| {key.replace('_', ' ')} | {balanced['old'][key]['mean']} | {balanced['pilot'][key]['mean']} |"
        )
    lines += [
        "",
        "These are descriptive results on a designed cohort, not significance tests or prevalence estimates. The new generator intentionally balances branch coverage; the old generator did not use this same coverage target.",
        "",
        "## Feature coverage",
        "",
        "| Feature | Old resources | Pilot additions |",
        "|---|---:|---:|",
    ]
    for feature in sorted(set(old["features"]) | set(new["features"])):
        lines.append(
            f"| {feature} | {old['features'].get(feature, 0)} | {new['features'].get(feature, 0)} |"
        )
    lines += [
        "",
        "## Verification",
        "",
        f"- {summary['pilot_reference_tasks']} pilot CRUD/reference workflows replayed successfully.",
        f"- {summary['baseline_reference_tasks']} existing workflows replayed successfully on original shards.",
        f"- {summary['probe_queries']} exact-match query probes passed.",
        "- New resources passed R4 JSON schema, graph checks, and definition-derived root contracts.",
        "- Recompilation and resource-order reversal preserved resource sets and answers.",
        "- History sidecars retain previous versions; the current agent environment does not expose FHIR history.",
        "- Official-validator output, when available, is reported separately; terminology and independent-server replay are separate checks.",
        "",
        "See `review-pack.md`, `summary.json`, `cases.jsonl`, `events.jsonl` and `history.jsonl` in this directory. No models were run.",
    ]
    (output / "comparison.md").write_text("\n".join(lines) + "\n")


def review_pack(output, cases, old_by_patient, new_by_patient):
    selections = [
        (0, "lab"),
        (4, "lab"),
        (8, "lab"),
        (3, "lab"),
        (2, "medication"),
        (1, "insurance"),
        (0, "scheduling"),
    ]
    categories = {
        "lab": {"ServiceRequest", "Specimen", "Observation", "DiagnosticReport"},
        "medication": {
            "Medication",
            "MedicationRequest",
            "MedicationDispense",
            "MedicationStatement",
        },
        "insurance": {
            "Coverage",
            "CoverageEligibilityRequest",
            "CoverageEligibilityResponse",
        },
        "scheduling": {"Schedule", "Slot", "Appointment"},
    }
    lines = [
        "# Old-versus-pilot resource review pack",
        "",
        "This is a selected synthetic dev sample. Inspect raw files for complete charts. These examples are not clinician-reviewed.",
        "",
    ]
    for i, domain in selections:
        case = cases[i]
        pid = case["patient_id"]
        lines += [
            f"## Case {i}: {domain}; age {case['age']}; {case['lab_branch']}/{case['panel_encoding']}",
            "",
            f"Patient `{pid}`; snapshot {case['snapshot_date']}. Old: `old/{pid}.ndjson`. Candidate additions: `new/{pid}.ndjson`.",
            "",
        ]
        for name, rows in (
            ("Old generated sample", old_by_patient[pid]),
            ("Pilot additions", new_by_patient[pid]),
        ):
            picked = [
                r
                for r in rows
                if generated(r) and r["resourceType"] in categories[domain]
            ]
            if name.startswith("Old"):
                # Spread through sorted records rather than selecting the most favorable example.
                picked = sorted(picked, key=lambda r: (r["resourceType"], r["id"]))
                by_type = defaultdict(list)
                for r in picked:
                    by_type[r["resourceType"]].append(r)
                picked = [r for kind in sorted(by_type) for r in by_type[kind][:2]]
            lines += ["### " + name, "", "```json"]
            for r in picked:
                excerpt = {
                    k: v for k, v in r.items() if k not in {"meta", "content", "text"}
                }
                lines.append(json.dumps(excerpt, indent=2))
            lines += ["```", ""]
    (output / "review-pack.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("data/synthetic-hospital-v0.3.0")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/generation-pilot")
    )
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()
    source, output = args.source, args.output
    manifest_before = digest(source / "manifest.json")
    tasks = load_jsonl(source / "dev/tasks.jsonl")
    cohort, shards = choose_cohort(source, tasks)
    cases, compiled_tasks, events, history, queries = [], [], [], [], []
    old_by_patient, new_by_patient = {}, {}
    for directory in ("old", "new", "dev/shards", "validator-inputs"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    for i, (patient, source_task, age, band) in enumerate(cohort):
        pid = patient["id"]
        baseline = closure(shards[source_task["shard"]], patient)
        plan = plan_episode(patient, source_task["snapshot_date"], i, args.seed)
        compiled = compile_episode(patient, plan)
        if compiled != compile_episode(patient, plan):
            raise ValueError("Nondeterministic event compiler")
        merged = [*baseline, *compiled["resources"]]
        validate_graph(merged)
        shard = f"dev/shards/{i:04}.ndjson"
        write_jsonl(output / shard, merged)
        write_jsonl(output / "old" / (pid + ".ndjson"), baseline)
        write_jsonl(output / "new" / (pid + ".ndjson"), compiled["resources"])
        old_by_patient[pid], new_by_patient[pid] = baseline, compiled["resources"]
        new_tasks = make_tasks(patient, compiled, shard)
        for task in new_tasks:
            audit_discovery(task, merged)
        compiled_tasks += new_tasks
        cases.append(
            {**plan, "band": band, "labels": compiled["labels"], "shard": shard}
        )
        events += [{"patient_id": pid, **e} for e in compiled["events"]]
        history += compiled["history"]
        queries += [
            {"patient_id": pid, **q} for q in probe_queries(compiled, patient, baseline)
        ]
        # Collection bundles resolve relative references for independent validation.
        for name, records in (
            ("old", baseline),
            ("pilot", [patient, *compiled["resources"]]),
        ):
            bundle = {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [
                    {
                        "fullUrl": "https://fhir-query-rl.example/fhir/" + ref(r),
                        "resource": r,
                    }
                    for r in records
                ],
            }
            (output / "validator-inputs" / f"{name}-{i:02}.json").write_text(
                json.dumps(bundle, indent=2) + "\n"
            )
    write_jsonl(output / "dev/tasks.jsonl", compiled_tasks)
    write_jsonl(output / "cases.jsonl", cases)
    write_jsonl(output / "events.jsonl", events)
    write_jsonl(output / "history.jsonl", history)
    write_jsonl(output / "queries.jsonl", queries)
    pilot_replays = [replay_task(output, t) for t in compiled_tasks]
    ids = {p["id"] for p, _, _, _ in cohort}
    old_tasks = [t for t in tasks if t["patient_id"] in ids]
    baseline_replays = [replay_task(source, t) for t in old_tasks]
    write_jsonl(output / "pilot-replays.jsonl", pilot_replays)
    write_jsonl(output / "old-replays.jsonl", baseline_replays)
    # Reordering resources must not change answers or resource identity; no new simulation.
    for i, (_, source_task, _, _) in enumerate(cohort):
        shard = output / f"dev/shards/{i:04}.ndjson"
        write_jsonl(shard, list(reversed(load_jsonl(shard))))
    for task in compiled_tasks:
        replay_task(output, task)
    old = [
        r
        for rows in old_by_patient.values()
        for r in rows
        if generated(r) and r["resourceType"] != "Provenance"
    ]
    new = [r for rows in new_by_patient.values() for r in rows]
    summary = {
        "generator": PILOT_VERSION,
        "seed": args.seed,
        "patients": len(cohort),
        "age_bands": dict(Counter(c["band"] for c in cases)),
        "branch_counts": dict(Counter(c["lab_branch"] for c in cases)),
        "encoding_counts": dict(Counter(c["panel_encoding"] for c in cases)),
        "source_manifest_sha256": manifest_before,
        "catalog_sha256": digest(CATALOG_PATH),
        "old_generated_resources": len(old),
        "pilot_resources": len(new),
        "old_generated_types": len({r["resourceType"] for r in old}),
        "pilot_generated_types": len({r["resourceType"] for r in new}),
        "old_common_types": describe(old),
        "pilot_common_types": describe(new),
        "balanced": balanced_comparison(old, new, args.seed),
        "pilot_reference_tasks": len(pilot_replays),
        "baseline_reference_tasks": len(baseline_replays),
        "probe_queries": sum("status" in q for q in queries),
        "discovery_audited_tasks": len(compiled_tasks),
        "history_versions": len(history),
        "model_rollouts": 0,
        "limitations": [
            "Designed dev cohort; not population representative",
            "Different workflow scope, not a generator-quality significance test",
            "No Synthea, LLM generation, clinical review or independent-server replay",
            "Current environment does not expose hasMember includes, history or full FHIR search semantics",
        ],
    }
    if digest(source / "manifest.json") != manifest_before:
        raise ValueError("Source manifest changed")
    summary["source_files_unchanged"] = all(
        digest(source / path) == expected
        for path, expected in json.loads((source / "manifest.json").read_text())[
            "files"
        ].items()
    )
    if not summary["source_files_unchanged"]:
        raise ValueError("Frozen source file checksum mismatch")
    summary["files"] = {
        str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*.ndjson"))
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_report(output, summary)
    review_pack(output, cases, old_by_patient, new_by_patient)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "patients",
                    "pilot_resources",
                    "pilot_reference_tasks",
                    "baseline_reference_tasks",
                    "probe_queries",
                    "balanced",
                    "source_files_unchanged",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
