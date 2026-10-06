"""Truthful answer/evidence and whole-state-delta verification."""

import copy

from .protocol import parse_object
from .validation import patient_ref, validate_graph


def strip_version(resource):
    r = dict(resource) if resource else resource
    if r and "meta" in r:
        r["meta"] = dict(r["meta"])
        r["meta"].pop("versionId", None)
        if not r["meta"]:
            r.pop("meta")
    return r


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(subset(e, a) for e, a in zip(expected, actual))
        )
    return type(expected) is type(actual) and expected == actual


def answer_equal(expected, actual):
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(expected) == set(actual)
            and all(answer_equal(v, actual[k]) for k, v in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(answer_equal(e, a) for e, a in zip(expected, actual))
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    return expected == actual


def create_matches(pattern, resource):
    normalized = strip_version(resource)
    normalized.pop("id", None)
    return normalized == strip_version(pattern)


def mutation_integrity(task, store):
    """Audit every write, independently of whether the task is finished."""
    gold = task["gold"]
    # Every committed mutation must itself be allowed; restore-after-corrupt is a failure.
    changes = {}
    for u in gold["updates"]:
        changes.setdefault(u["ref"], {})[u["field"]] = u["value"]
    for entry in store.audit:
        key = entry["ref"]
        if entry["method"] == "DELETE":
            if key not in gold["deletes"]:
                return False
        elif entry["method"] == "POST":
            if not any(create_matches(p, entry["after"]) for p in gold["creates"]):
                return False
        else:
            if key not in changes:
                return False
            allowed = copy.deepcopy(entry["before"])
            for field, value in changes[key].items():
                allowed[field] = value
            if strip_version(entry["after"]) != strip_version(allowed):
                return False
    if task.get("requires_read_before_write") and store.audit:
        # Write responses cannot retrospectively satisfy pre-mutation evidence.
        read = set()
        for event in store.events:
            if event["status"] >= 300:
                continue
            if event["method"] in {"POST", "PUT", "DELETE"}:
                if not set(gold["evidence"]) <= read:
                    return False
            elif event["method"] == "GET" or event["route"] in {"sql", "context"}:
                for key in event["refs"]:
                    fields = event.get("exposed_fields", {}).get(key, [])
                    if set(gold.get("evidence_fields", {}).get(key, [])) <= set(fields):
                        read.add(key)
    if task.get("requires_transaction") and store.audit:
        committed = [
            e
            for e in store.events
            if e["status"] < 300 and e["method"] in {"POST", "PUT", "DELETE"}
        ]
        if (
            len(committed) != 1
            or committed[0]["query"] != ""
            or committed[0]["primitive_operations"]
            != len(changes) + len(gold["creates"]) + len(gold["deletes"])
        ):
            return False
    try:
        validate_graph(list(store.resources.values()), validate_shapes=False)
    except ValueError:
        return False
    return not store.unsafe_attempts


def state_success(task, store):
    gold = task["gold"]
    expected = store.initial.copy()
    for change in gold["updates"]:
        expected[change["ref"]] = dict(expected[change["ref"]])
        expected[change["ref"]][change["field"]] = change["value"]
    for key in gold["deletes"]:
        expected.pop(key)
    if any(
        key not in store.resources
        or strip_version(value) != strip_version(store.resources[key])
        for key, value in expected.items()
    ):
        return False
    added = [r for key, r in store.resources.items() if key not in store.initial]
    if len(added) != len(gold["creates"]):
        return False
    for pattern in gold["creates"]:
        candidates = [r for r in added if create_matches(pattern, r)]
        if len(candidates) != 1:
            return False
        added.remove(candidates[0])
    if set(store.initial) - set(store.resources) != set(gold["deletes"]):
        return False
    return mutation_integrity(task, store)


def absence_checked(task, store):
    return not task.get("absence_scope") or absence_call(task, store) is not None


def absence_call(task, store):
    scope = task.get("absence_scope")
    if not scope:
        return 0
    # Require an executed, bounded read over the relevant accessible patient/type/code.
    from urllib.parse import parse_qs, urlsplit

    for i, event in enumerate(store.events, 1):
        if (
            event["status"] >= 300
            or event["route"] != "rest"
            or event["method"] != "GET"
        ):
            continue
        parsed = urlsplit(event["query"])
        params = parse_qs(parsed.query)
        if parsed.path.strip("/") != scope["resourceType"]:
            continue
        allowed = {"patient", "_count"} | ({"type"} if "code" in scope else set())
        if set(params) - allowed or len(params.get("patient", [])) != 1:
            continue
        patient = params.get("patient", [])
        if (
            scope["patient"] not in patient
            and scope["patient"].split("/")[1] not in patient
        ):
            continue
        if ("code" not in scope or params.get("type") == [scope["code"]]) and event.get(
            "total"
        ) == 0:
            return i
    # SQL abstention is intentionally not scored yet: proving an equivalent search scope
    # with joins/anti-joins needs provenance instrumentation beyond our pilot.
    return None


def evaluate(task, store, final, efficiency_weight=0.0):
    if not 0 <= efficiency_weight <= 0.15:
        raise ValueError("efficiency_weight must be in [0, 0.15]")
    if isinstance(final, str):
        try:
            final = parse_object(final)
        except (ValueError, TypeError):
            final = {}
    if not isinstance(final, dict):
        final = {}
    submitted = final.get("evidence", [])
    submitted = (
        set(submitted)
        if isinstance(submitted, list) and all(isinstance(x, str) for x in submitted)
        else set()
    )
    required = set(task["gold"]["evidence"])
    eligible = {
        key
        for key in store.seen
        if set(task["gold"].get("evidence_fields", {}).get(key, []))
        <= store.exposed.get(key, set())
    }
    relevant = submitted & required & eligible
    recall = len(relevant) / len(required) if required else 1.0
    precision = len(relevant) / len(submitted) if submitted else float(not required)
    answer_ok = set(final) == {"answer", "evidence"} and answer_equal(
        task["gold"]["answer"], final.get("answer")
    )
    delta_ok = state_success(task, store)
    success = (
        answer_ok
        and recall == 1
        and precision == 1
        and delta_ok
        and absence_checked(task, store)
    )
    targets = set(task["gold"].get("target_evidence", required))
    seen = set()
    first = None
    all_at = None
    first_round = None
    all_round = None
    absent_at = absence_call(task, store)
    for i, event in enumerate(store.events, 1):
        if event["route"] == "context":
            i = 0
        found = set(event["refs"]) & required
        if "exposed_fields" in event:
            found = {
                key
                for key in found
                if set(task["gold"].get("evidence_fields", {}).get(key, []))
                <= set(event.get("exposed_fields", {}).get(key, []))
            }
        if found & targets and first is None:
            first = i
            first_round = event["round"]
        seen.update(found)
        if (
            required <= seen
            and absent_at is not None
            and i >= absent_at
            and all_at is None
        ):
            all_at = i
            all_round = event["round"]
    requests = [e for e in store.events if e["route"] != "context"]
    reads = [e for e in requests if e["method"] in {"GET", "SELECT"}]
    read_refs = {key for event in reads for key in event["refs"]}
    cross_patient = {
        key
        for key in read_refs
        if key in store.initial
        and patient_ref(store.initial[key])
        not in {None, "Patient/" + task["patient_id"]}
    }
    excess = max(0, len(requests) - task["reference_calls"])
    reward = (
        (1 - efficiency_weight * min(1, excess / store.max_calls)) if success else 0.0
    )
    return {
        "reward": reward,
        "strict_success": float(success),
        "answer_success": float(answer_ok),
        "state_success": float(delta_ok),
        "evidence_recall": recall,
        "evidence_precision": precision,
        "tool_calls": len(requests),
        "agent_tool_calls": len(store.agent_tools),
        "helper_calls": len(store.assistance),
        "helper_response_bytes": sum(e["bytes"] for e in store.assistance),
        "primitive_operations": sum(e["primitive_operations"] for e in store.events),
        "response_bytes": sum(e["bytes"] for e in requests),
        "context_bytes": sum(
            e["bytes"] for e in store.events if e["route"] == "context"
        ),
        "first_evidence_call": first,
        "first_evidence_round": first_round,
        "all_evidence_call": all_at,
        "all_evidence_round": all_round,
        "first_evidence_applicable": int(bool(targets)),
        "first_evidence_censored": int(bool(targets) and first is None),
        "all_evidence_censored": int(all_at is None),
        "tool_errors": sum(e["status"] >= 400 for e in store.events),
        "unsafe_attempts": store.unsafe_attempts,
        "read_calls": len(reads),
        "write_calls": sum(e["method"] in {"POST", "PUT", "DELETE"} for e in requests),
        "committed_mutations": len(store.audit),
        "unique_read_resources": len(read_refs),
        "non_gold_read_resources": len(read_refs - required),
        "cross_patient_read_resources": len(cross_patient),
    }


def evaluate_training(
    task,
    store,
    final,
    efficiency_weight=0.0,
    answer_mode="strict",
    retrieval_weight=0.0,
):
    """Keep strict evaluation while optionally shaping verified retrieval only."""
    if answer_mode not in {"strict", "json_or_fence"}:
        raise ValueError("answer_mode must be strict or json_or_fence")
    if not 0 <= retrieval_weight <= 0.2:
        raise ValueError("retrieval_weight must be in [0,0.2]")
    metrics = evaluate(task, store, final, efficiency_weight)
    try:
        parsed = (
            parse_object(final, allow_fence=True) if isinstance(final, str) else final
        )
    except (ValueError, TypeError):
        parsed = {}
    canonical = evaluate(task, store, parsed, efficiency_weight)
    metrics["canonical_success"] = canonical["strict_success"]
    metrics["json_format_valid"] = int(
        isinstance(parsed, dict) and set(parsed) == {"answer", "evidence"}
    )
    selected = canonical if answer_mode == "json_or_fence" else metrics
    metrics["workflow_success"] = selected["strict_success"]
    metrics["reward"] = selected["reward"]
    required = set(task["gold"]["evidence"])
    eligible = {
        key
        for key in store.seen
        if set(task["gold"].get("evidence_fields", {}).get(key, []))
        <= store.exposed.get(key, set())
    }
    progress = len(required & eligible) / len(required) if required else 0.0
    # Prompt-supplied context is not retrieval; unchanged state alone earns nothing.
    attempted = any(e["route"] != "context" for e in store.events)
    safe = mutation_integrity(task, store)
    metrics["retrieval_progress"] = progress
    metrics["mutation_integrity"] = int(safe)
    metrics["retrieval_shaping"] = (
        retrieval_weight * progress
        if not metrics["workflow_success"] and attempted and safe
        else 0.0
    )
    metrics["reward"] += metrics["retrieval_shaping"]
    # -1 means not observed. Pair these numeric counters with censor/applicability
    # flags; never average -1 into a successful search-latency estimate.
    for name in (
        "first_evidence_call",
        "first_evidence_round",
        "all_evidence_call",
        "all_evidence_round",
    ):
        metrics[name + "_observed"] = metrics[name] if metrics[name] is not None else -1
    features = [
        e.get("query_features", {})
        for e in store.events
        if e["route"] in {"rest", "sql"}
        and e["method"] in {"GET", "SELECT"}
        and e["query"] != "metadata"
    ]
    for name in (
        "characters",
        "parameter_count",
        "predicate_count",
        "chain_depth",
        "includes",
        "joins",
    ):
        values = [f.get(name, 0) for f in features]
        metrics["query_" + name + "_mean"] = (
            sum(values) / len(values) if values else 0.0
        )
        metrics["query_" + name + "_max"] = max(values, default=0)
    from .metrics import search_behavior

    metrics.update(search_behavior(task, store))
    return metrics
