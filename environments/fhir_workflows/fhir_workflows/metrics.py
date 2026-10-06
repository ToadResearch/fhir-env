"""Descriptive structural metrics, never rewarded directly."""

from collections.abc import Mapping
import math
import re
from urllib.parse import parse_qsl, urlsplit

import sqlglot
from sqlglot import exp


def lexical_terms(text):
    """Case-folded lexical tokens; this is not a clinical concept extractor."""
    return set(re.findall(r"[\w]+(?:[-./][\w]+)*", text.casefold()))


def filter_terms(event):
    """Terms in filter values, excluding transport controls and SQL JSON paths."""
    if event["route"] == "rest":
        values = [
            value.rsplit("|", 1)[-1]
            for key, value in parse_qsl(urlsplit(event["query"]).query)
            if not key.startswith("_") or key == "_id"
        ]
    elif event["route"] == "sql":
        try:
            ast = sqlglot.parse_one(event["query"], read="sqlite")
            where = ast.args.get("where")
            values = (
                [
                    str(n.this)
                    for n in where.find_all(exp.Literal)
                    if not str(n.this).startswith("$")
                ]
                if where
                else []
            )
        except sqlglot.errors.ParseError:
            values = []
    else:
        values = []
    values = [
        re.sub(r"^(?:eq|ne|gt|lt|ge|le|sa|eb|ap)(?=\d{4}-)", "", v) for v in values
    ]
    return lexical_terms(" ".join(values))


def task_dimensions(task):
    """Fixed annotations, independent of agent behavior or checkpoint scores."""
    hops = task["dependency_depth"]
    gold = task["gold"]
    writes = sum(len(gold.get(key, [])) for key in ("creates", "updates", "deletes"))
    crud = "".join(
        letter
        for letter, present in (
            ("C", bool(gold.get("creates"))),
            ("R", True),
            ("U", bool(gold.get("updates"))),
            ("D", bool(gold.get("deletes"))),
        )
        if present
    )
    return {
        "retrieval_hops": hops,
        "difficulty_bin": "easy"
        if hops <= 1
        else "medium"
        if hops == 2
        else "hard"
        if hops == 3
        else "very_hard",
        "difficulty_basis": "Declared information dependencies; not proven minimum calls",
        "workflow_depth": task.get("workflow_depth", hops + int(bool(writes))),
        "required_evidence_resources": len(gold["evidence"]),
        "required_mutation_entries": writes,
        "crud": crud,
        "reference_calls": task["reference_calls"],
        "requires_transaction": bool(task.get("requires_transaction")),
    }


def search_behavior(task, store):
    """Descriptive behavior metrics over visible retrieval, never reward inputs."""
    reads = [
        e
        for e in store.events
        if e["route"] in {"rest", "sql"}
        and e["method"] in {"GET", "SELECT"}
        and e["query"].strip("/") != "metadata"
    ]
    searches = [
        e
        for e in reads
        if e["route"] == "sql"
        or (
            urlsplit(e["query"]).path.strip("/")
            and "/" not in urlsplit(e["query"]).path.strip("/")
        )
    ]
    gold = set(task["gold"].get("target_evidence", task["gold"]["evidence"]))
    first = next((e for e in store.events if e["route"] != "context"), None)
    first_tool = (
        store.agent_tools[0] if store.agent_tools else (first or {}).get("via_tool")
    )
    # Active episode rounds start at one. An invalid JSON action or an earlier
    # round without a backend event must not move the definition of first turn.
    first_round = 1
    first_search = searches[0] if searches else None

    def targeted(e):
        return bool(filter_terms(e))

    def eligible(e):
        return {
            key
            for key in set(e["refs"]) & gold
            if set(task["gold"].get("evidence_fields", {}).get(key, []))
            <= set(e.get("exposed_fields", {}).get(key, []))
        }

    first_is_search = bool(
        first
        and first in searches
        and first_tool not in {"fhir_schema", "read_document", "prepare_update"}
    )
    novel = set()
    query_terms = set()
    prompt_terms = lexical_terms(task["prompt"])
    for e in searches:
        terms = filter_terms(e)
        query_terms.update(terms)
        novel.update(
            set(e.get("filter_terms_not_previously_observed", [])) - prompt_terms
        )
    # Older saved traces lack observation accounting; do not report false zeroes.
    novelty_available = bool(searches) and all(
        "filter_terms_not_previously_observed" in e for e in searches
    )
    lengths = [len(set(e["refs"])) for e in searches]
    backend_lengths = [len(set(e.get("backend_refs", e["refs"]))) for e in searches]
    return {
        "retrieval_hops": task["dependency_depth"],
        "workflow_depth": task_dimensions(task)["workflow_depth"],
        "required_evidence_resources": len(task["gold"]["evidence"]),
        "search_calls": len(searches),
        "search_rounds": len({e["round"] for e in searches}),
        "first_action_targeted_search": int(first_is_search and targeted(first)),
        "first_action_broad_search": int(first_is_search and not targeted(first)),
        "first_action_document_read": int(
            first_tool == "read_document"
            or bool(
                first
                and first_tool in {None, "fhir_request"}
                and first["method"] == "GET"
                and first["query"].startswith("DocumentReference/")
                and "?" not in first["query"]
            )
        ),
        "first_turn_targeted_search": int(
            any(targeted(e) for e in searches if e["round"] == first_round)
        ),
        "first_search_gold_resource_hit": int(
            bool(first_search and set(first_search["refs"]) & gold)
        ),
        "first_search_required_fields_hit": int(
            bool(first_search and eligible(first_search))
        ),
        "first_turn_gold_resource_hit": int(
            any(set(e["refs"]) & gold for e in reads if e["round"] == first_round)
        ),
        "first_turn_required_fields_hit": int(
            any(eligible(e) for e in reads if e["round"] == first_round)
        ),
        "unique_resources_per_search_mean": sum(lengths) / len(lengths)
        if lengths
        else 0.0,
        "backend_resources_per_search_mean": sum(backend_lengths) / len(backend_lengths)
        if backend_lengths
        else 0.0,
        "gold_resource_search_hit_rate": sum(
            bool(set(e["refs"]) & gold) for e in searches
        )
        / len(searches)
        if searches
        else 0.0,
        "repeated_queries": len(reads)
        - len({(e["route"], e["method"], e["query"]) for e in reads}),
        "distinct_filter_terms": len(query_terms),
        "novel_filter_terms": len(novel) if novelty_available else -1,
        "filter_novelty_observed": int(novelty_available),
    }


def rollout_usage_metrics(trajectory):
    """Sum API usage across model calls. Missing usage is never zero tokens.

    Input totals include repeated context on subsequent calls. Reasoning tokens,
    when reported, are already a subset of completion tokens, so are not added.
    """
    turns = len(trajectory)
    measured = []
    for step in trajectory:
        response = step.get("response")
        usage = (
            response.get("usage")
            if isinstance(response, Mapping)
            else getattr(response, "usage", None)
        )
        if usage is None:
            continue
        incoming = (
            usage.get("prompt_tokens")
            if isinstance(usage, Mapping)
            else getattr(usage, "prompt_tokens", None)
        )
        outgoing = (
            usage.get("completion_tokens")
            if isinstance(usage, Mapping)
            else getattr(usage, "completion_tokens", None)
        )
        if all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and v >= 0
            for v in (incoming, outgoing)
        ):
            measured.append((incoming, outgoing))
    complete = bool(turns) and len(measured) == turns
    incoming, outgoing = (sum(v[0] for v in measured), sum(v[1] for v in measured))
    return {
        "model_turns": turns,
        "token_usage_complete": int(complete),
        "token_usage_call_coverage": len(measured) / turns if turns else 0.0,
        "model_input_tokens": incoming if complete else -1,
        "model_output_tokens": outgoing if complete else -1,
        "model_total_tokens": incoming + outgoing if complete else -1,
        "observed_model_input_tokens": incoming,
        "observed_model_output_tokens": outgoing,
    }


def query_features(event):
    query = event["query"]
    result = {
        "characters": len(query),
        "utf8_bytes": len(query.encode()),
        "route": event["route"],
    }
    if event["route"] == "rest":
        pairs = parse_qsl(urlsplit(query).query)
        keys = [p[0] for p in pairs]
        result.update(
            {
                "parameter_count": len(keys),
                "distinct_parameters": len(set(keys)),
                "predicate_count": sum(
                    not k.startswith("_") or k == "_id" for k in keys
                ),
                "chain_depth": max((k.count(".") for k in keys), default=0),
                "includes": sum(k in {"_include", "_revinclude"} for k in keys),
                "reverse_chains": sum(k.startswith("_has:") for k in keys),
                "temporal_filters": sum(k == "date" for k in keys),
            }
        )
    else:
        try:
            ast = sqlglot.parse_one(query, read="sqlite")
            result.update(
                {
                    "joins": len(list(ast.find_all(exp.Join))),
                    "subqueries": len(list(ast.find_all(exp.Subquery))),
                    "predicate_count": sum(
                        isinstance(
                            n,
                            (
                                exp.EQ,
                                exp.NEQ,
                                exp.GT,
                                exp.GTE,
                                exp.LT,
                                exp.LTE,
                                exp.In,
                                exp.Is,
                                exp.Like,
                            ),
                        )
                        for n in ast.walk()
                    ),
                    "selected_columns": len(ast.expressions),
                    "group_by": int(ast.args.get("group") is not None),
                }
            )
        except sqlglot.errors.ParseError:
            result["parse_error"] = True
    return result
