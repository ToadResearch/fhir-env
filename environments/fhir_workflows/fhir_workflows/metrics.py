"""Descriptive structural metrics, never rewarded directly."""

from urllib.parse import parse_qsl, urlsplit

import sqlglot
from sqlglot import exp


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
