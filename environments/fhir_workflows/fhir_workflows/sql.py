"""Experimental read-only SQLite projections; not a SQL-on-FHIR runner."""

import json
import sqlite3

import sqlglot
from sqlglot import exp

from .store import FhirError, resource_date
from .validation import patient_ref, ref

SCHEMA = {
    "Patient": ("resource_ref", "id", "identifier", "name", "birth_date", "gender"),
    "Observation": (
        "resource_ref",
        "id",
        "patient_ref",
        "code",
        "status",
        "date",
        "value",
        "unit",
    ),
    "ServiceRequest": (
        "resource_ref",
        "id",
        "patient_ref",
        "identifier",
        "code",
        "status",
        "date",
    ),
    "DiagnosticReport": (
        "resource_ref",
        "id",
        "patient_ref",
        "code",
        "status",
        "date",
        "based_on_ref",
        "result_ref",
    ),
    "Coverage": (
        "resource_ref",
        "id",
        "patient_ref",
        "status",
        "subscriber_id",
        "payor_ref",
        "start_date",
        "end_date",
    ),
    "Task": ("resource_ref", "id", "patient_ref", "status", "focus_ref", "description"),
    "Communication": ("resource_ref", "id", "patient_ref", "status", "payload", "date"),
    "DocumentReference": (
        "resource_ref",
        "id",
        "patient_ref",
        "status",
        "code",
        "title",
    ),
    "Procedure": ("resource_ref", "id", "patient_ref", "code", "status", "date"),
}
SCHEMA["Resource"] = ("resource_ref", "resource_type", "patient_ref", "resource_json")
FIELD_MAP = {
    "identifier": "identifier",
    "name": "name",
    "birth_date": "birthDate",
    "gender": "gender",
    "patient_ref": "subject",
    "code": "code",
    "status": "status",
    "value": "valueQuantity",
    "unit": "valueQuantity",
    "based_on_ref": "basedOn",
    "result_ref": "result",
    "subscriber_id": "subscriberId",
    "payor_ref": "payor",
    "start_date": "period",
    "end_date": "period",
    "focus_ref": "focus",
    "description": "description",
    "payload": "payload",
    "title": "content",
}


def code(resource):
    c = resource.get("code", resource.get("type", {}))
    return c.get("coding", [{}])[0].get("code")


def first_ref(resource, field):
    value = resource.get(field, [])
    if isinstance(value, dict):
        return value.get("reference")
    return value[0].get("reference") if value else None


def project(resource):
    kind = resource["resourceType"]
    if kind not in SCHEMA:
        return []
    row = {
        "resource_ref": ref(resource),
        "id": resource["id"],
        "patient_ref": patient_ref(resource),
        "code": code(resource),
        "status": resource.get("status"),
        "date": resource_date(resource),
        "identifier": resource.get("identifier", [{}])[0].get("value"),
        "name": resource.get("name", [{}])[0].get("text")
        if isinstance(resource.get("name", []), list)
        else resource.get("name"),
        "birth_date": resource.get("birthDate"),
        "gender": resource.get("gender"),
        "value": resource.get("valueQuantity", {}).get("value"),
        "unit": resource.get("valueQuantity", {}).get("unit"),
        "based_on_ref": first_ref(resource, "basedOn"),
        "result_ref": first_ref(resource, "result"),
        "subscriber_id": resource.get("subscriberId"),
        "payor_ref": first_ref(resource, "payor"),
        "start_date": resource.get("period", {}).get("start"),
        "end_date": resource.get("period", {}).get("end"),
        "focus_ref": first_ref(resource, "focus"),
        "description": resource.get("description"),
        "payload": resource.get("payload", [{}])[0].get("contentString"),
        "title": resource.get("content", [{}])[0].get("attachment", {}).get("title"),
    }
    # one report row per result; preserve array cardinality rather than losing results
    if kind == "DiagnosticReport":
        return [
            {
                **row,
                "result_ref": result.get("reference"),
                "based_on_ref": order.get("reference"),
            }
            for result in (resource.get("result") or [{}])
            for order in (resource.get("basedOn") or [{}])
        ]
    return [row]


def query(store, sql):
    rows = []
    matched_refs = set()
    exposed_fields = {}
    status = 200
    con = None
    try:
        if len(store.events) >= store.max_calls:
            raise FhirError(429, "Episode tool-call budget exhausted")
        if len(sql) > 8192:
            raise ValueError("SQL length limit exceeded")
        parsed = sqlglot.parse(sql, read="sqlite")
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
            raise ValueError(
                "Exactly one SELECT statement is supported; no CTEs or writes"
            )
        ast = parsed[0]
        # Field-exposure accounting below supports direct base-table columns only.
        # Derived columns can reuse a field name while returning a constant, so
        # accepting nested SELECTs would credit evidence that was never exposed.
        if (
            ast.find(exp.Subquery)
            or ast.find(exp.CTE)
            or ast.find(exp.With)
            or len(list(ast.find_all(exp.Select))) != 1
        ):
            raise ValueError(
                "Subqueries, derived tables and CTEs require unsupported evidence lineage; use direct base-table joins"
            )
        if any(
            isinstance(
                n,
                (exp.Command, exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop),
            )
            for n in ast.walk()
        ):
            raise ValueError("Read-only SELECT only")
        tables = {t.name for t in ast.find_all(exp.Table)}
        if not tables or not tables <= SCHEMA.keys():
            raise ValueError("Query only the documented projection tables")
        if any(c.name == "*" for c in ast.find_all(exp.Column)) or ast.find(exp.Star):
            raise ValueError("Select explicit columns and resource_ref for citations")
        con = sqlite3.connect(":memory:")
        for kind, columns in SCHEMA.items():
            con.execute(
                'CREATE TABLE "'
                + kind
                + '" ('
                + ",".join('"' + c + '"' for c in columns)
                + ")"
            )
        for resource in store.resources.values():
            con.execute(
                'INSERT INTO "Resource" VALUES (?,?,?,?)',
                (
                    ref(resource),
                    resource["resourceType"],
                    patient_ref(resource),
                    json.dumps(resource, sort_keys=True),
                ),
            )
            kind = resource["resourceType"]
            if kind not in SCHEMA:
                continue
            columns = SCHEMA[kind]
            for row in project(resource):
                con.execute(
                    'INSERT INTO "'
                    + kind
                    + '" VALUES ('
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(row.get(c) for c in columns),
                )
        # Defense in depth: after construction, enforce SQLite's own read-only policy.
        con.execute("PRAGMA query_only=ON")

        def authorize(action, arg1, arg2, db, trigger):
            if action == sqlite3.SQLITE_FUNCTION:
                allowed = {
                    "count",
                    "sum",
                    "avg",
                    "min",
                    "max",
                    "coalesce",
                    "nullif",
                    "lower",
                    "upper",
                    "length",
                    "date",
                    "strftime",
                    "row_number",
                    "rank",
                    "dense_rank",
                    "round",
                    "abs",
                    "like",
                    "glob",
                    "json_extract",
                    "json_type",
                }
                return (
                    sqlite3.SQLITE_OK
                    if str(arg2).lower() in allowed
                    else sqlite3.SQLITE_DENY
                )
            return (
                sqlite3.SQLITE_OK
                if action
                in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
                else sqlite3.SQLITE_DENY
            )

        con.set_authorizer(authorize)
        ticks = 0

        def budget():
            nonlocal ticks
            ticks += 1
            return int(ticks > 2000)

        con.set_progress_handler(budget, 1000)
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        if len(names) != len(set(names)):
            raise ValueError("Use unique aliases for projected columns")
        fetched = cur.fetchmany(51)
        truncated = len(fetched) > 50
        rows = [dict(zip(names, row)) for row in fetched[:50]]
        # Conservatively require a returned resource_ref column for each evidence resource.
        # The SQL AST ties field aliases to the same table alias; IDs alone reveal no values.
        aliases = {t.alias_or_name: t.name for t in ast.find_all(exp.Table)}
        selected = {}
        for expression in ast.expressions:
            value = expression.this if isinstance(expression, exp.Alias) else expression
            if isinstance(value, exp.Column):
                table = value.table or (
                    next(iter(aliases)) if len(aliases) == 1 else ""
                )
                # SQLite's double-quoted-string compatibility can turn an
                # unknown "column" into a literal. Never treat such literals,
                # or unresolved multi-table columns, as exposed chart fields.
                if table not in aliases or value.name not in SCHEMA[aliases[table]]:
                    continue
                selected[expression.alias_or_name] = (table, value.name)
        projected_columns = {}
        for table, column in selected.values():
            projected_columns.setdefault(table, set()).add(column)
        for row in rows:
            for output, (table, column) in selected.items():
                target = row.get(output)
                if column != "resource_ref" or target not in store.resources:
                    continue
                fields = set()
                for other, (same_table, source) in selected.items():
                    if same_table != table:
                        continue
                    kind = store.resources[target]["resourceType"]
                    if source == "resource_json" and row.get(other) == json.dumps(
                        store.resources[target], sort_keys=True
                    ):
                        fields.update(store.resources[target])
                        continue
                    actual = (
                        "effectiveDateTime"
                        if source == "date" and kind == "Observation"
                        else "performedDateTime"
                        if source == "date" and kind == "Procedure"
                        else "type"
                        if source == "code" and kind == "DocumentReference"
                        else FIELD_MAP.get(source, source)
                    )
                    if actual == "valueQuantity" and (
                        not {"value", "unit"} <= projected_columns[table]
                        or "valueQuantity" not in store.resources[target]
                    ):
                        # Seeing a unit is not seeing the measured number. Both
                        # scalar columns must come from this same resource alias.
                        continue
                    fields.add(actual)
                store.observe(store.resources[target], fields=fields)
                matched_refs.add(target)
                exposed_fields[target] = sorted(fields)
        result = {"status": 200, "columns": names, "rows": rows, "truncated": truncated}
    except (FhirError, ValueError, sqlglot.errors.ParseError, sqlite3.Error) as error:
        status = error.status if isinstance(error, FhirError) else 400
        result = {"status": status, "error": str(error), "rows": []}
    finally:
        if con is not None:
            con.close()
    store.events.append(
        {
            "route": "sql",
            "method": "SELECT",
            "query": sql,
            "status": status,
            "round": store.round,
            "refs": sorted(matched_refs),
            "exposed_fields": exposed_fields,
            "bytes": len(json.dumps(result).encode()),
            "rows": len(rows),
            "primitive_operations": 1,
        }
    )
    from .metrics import filter_terms, query_features

    store.events[-1]["query_features"] = query_features(store.events[-1])
    store.events[-1]["filter_terms_not_previously_observed"] = sorted(
        filter_terms(store.events[-1]) - store.observed_terms
    )
    store.observe_response_terms(json.dumps(result))
    return result
