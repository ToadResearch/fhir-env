"""Prime legacy environment loader for verifiers 0.2.2.dev6."""

import copy
import inspect
import json
from functools import lru_cache
from pathlib import Path

import verifiers as vf
from datasets import Dataset
from jsonschema import ValidationError, validate

from .dataset import load_jsonl
from .helpers import HELPERS
from .metrics import rollout_usage_metrics, task_dimensions
from .protocol import ACTION_INSTRUCTIONS, is_final, parse_object
from .scoring import evaluate_training
from .sql import SCHEMA, query
from .store import FhirStore
from .validation import patient_ref, ref

SYSTEM_PROMPT = """You handle explicitly requested operations in a synthetic FHIR R4 chart.
Use only the supplied tools; do not infer undocumented clinical facts. Resolve the
patient and cite retrieved Type/id evidence. Preserve unrelated fields. A request
to cancel an order is a status update, not DELETE. Literal DELETE is authorized
only for the named duplicate draft. Updates/deletes require current If-Match ETags
(the meta.versionId in a search result can construct W/\"version\"). Write dependent
changes as a transaction when requested. Return the specified JSON final answer.
Return only answer/evidence JSON, with no explanation or Markdown. Evidence
contains only the resources needed to support the requested answer or change.
For an MRN-addressed task, retrieve and cite the Patient that verifies that
identifier, then the requested clinical resources. For a known Patient/id, an
extra Patient citation is not needed unless the question asks for its fields.
This research simulator supports GET type/id, GET type?search, POST type, PUT
type/id, DELETE type/id, and POST empty path for a transaction Bundle. No PATCH,
upsert, history, _has, _revinclude, arbitrary chained search or network URLs.
Use GET metadata for resource search support. Additionally: patient.identifier
chaining on patient-compartment resources, _id, _count (1..50), _sort=date,-date,
_page and _include for DiagnosticReport:result, DiagnosticReport:based-on,
ServiceRequest:subject, Task:focus, Coverage:payor. Repeated predicates are AND,
comma values OR, token values are system|code, date precision is YYYY-MM-DD.
The Resource SQL projection (hybrid mode) provides resource_ref, resource_type,
patient_ref and lossless resource_json for every supported resource. Returning a
resource_ref alone does not count as reading its contents. Shared Slot writes
are scoped to this episode's explicitly authorized slots. Read case evidence
before writing; a write response does not count as pre-change verification.
Codes local to this synthetic chart are not clinical terminology recommendations.
"""


@lru_cache(maxsize=64)
def read_shard(path):
    return load_jsonl(path)


async def fhir_request(
    method: str, path: str, body_json: str = "", headers_json: str = "", _store=None
) -> str:
    """Execute a bounded relative FHIR R4 request in this isolated episode.

    Args:
        method: GET, POST, PUT or DELETE.
        path: Relative Type/id, Type?search, metadata, or empty string for a transaction.
        body_json: JSON resource or transaction Bundle, empty for GET/DELETE.
        headers_json: JSON object containing If-Match or If-None-Exist headers.
    """
    body = json.loads(body_json) if body_json else None
    headers = json.loads(headers_json) if headers_json else {}
    if not isinstance(headers, dict):
        raise ValueError("headers_json must be an object")
    return json.dumps(_store.request(method, path, body, headers), ensure_ascii=False)


async def sql_query(sql: str, _store=None) -> str:
    """SELECT from documented experimental SQLite projections. Return resource_ref
    for each cited resource, with the evidence fields needed by the task. Explicit
    columns only, unique aliases, 50 returned rows maximum. SQL cannot write.

    Args:
        sql: One bounded SQLite SELECT statement over the documented tables.
    """
    return json.dumps(query(_store, sql), ensure_ascii=False)


def final_text(completion):
    for message in reversed(completion):
        role = message.get("role") if isinstance(message, dict) else message.role
        content = (
            message.get("content") if isinstance(message, dict) else message.content
        )
        if role == "assistant" and isinstance(content, str):
            return content
    return ""


class FhirWorkflowEnv(vf.StatefulToolEnv):
    def __init__(
        self,
        data_dir,
        task_index,
        query_mode,
        tool_profile,
        efficiency_weight,
        tool_protocol,
        answer_mode,
        **kwargs,
    ):
        self.data_dir = Path(data_dir)
        self.task_index = task_index
        self.efficiency_weight = efficiency_weight
        self.tool_profile = tool_profile
        self.tool_protocol = tool_protocol
        self.answer_mode = answer_mode
        super().__init__(tools=[], **kwargs)
        if tool_profile != "none":
            self.add_tool(fhir_request, args_to_skip=["_store"])
        if query_mode == "hybrid" and tool_profile != "none":
            self.add_tool(sql_query, args_to_skip=["_store"])
        if tool_profile == "assisted":
            for helper in HELPERS:
                self.add_tool(helper, args_to_skip=["_store"])
        self.action_schemas = {tool.name: tool.parameters for tool in self.tool_defs}
        if tool_protocol == "json_action":
            # Keep the exact same callable registry, state, schemas and verifier.
            # Ordinary JSON turns replace provider-specific function-call parsing.
            self.action_schemas = copy.deepcopy(self.action_schemas)
            for name, schema in self.action_schemas.items():
                schema["required"] = [
                    key
                    for key, parameter in inspect.signature(
                        self.tool_map[name]
                    ).parameters.items()
                    if key != "_store" and parameter.default is inspect.Parameter.empty
                ]
            self.tool_defs = []

    async def setup_state(self, state):
        info = state["info"]
        if isinstance(info, str):
            info = json.loads(info)
        task = self.task_index[info["task_id"]]
        omitted = set(task["omit"])
        resources = [
            r
            for r in read_shard(str(self.data_dir / task["shard"]))
            if f"{r['resourceType']}/{r['id']}" not in omitted
        ]
        state["fhir_task"] = task
        state["action_format_errors"] = 0
        state["fhir_prompt_assistant_turns"] = sum(
            (m.get("role") if isinstance(m, dict) else m.role) == "assistant"
            for m in state.get("prompt", [])
        )
        state["fhir_store"] = FhirStore(
            resources,
            writable_patient="Patient/" + task["patient_id"],
            writable_types=task["writable_types"],
            delete_refs=task["delete_refs"],
            writable_refs=task.get("writable_refs", ()),
        )
        for message in state.get("prompt", []):
            content = (
                message.get("content") if isinstance(message, dict) else message.content
            )
            if isinstance(content, str):
                state["fhir_store"].observe_response_terms(content)
        if self.tool_profile == "none":
            visible = context_resources(resources, task["patient_id"])
            for resource in visible:
                state["fhir_store"].observe(resource)
            state["fhir_store"].events.append(
                {
                    "route": "context",
                    "method": "PROMPT",
                    "query": "visible-patient-chart",
                    "status": 200,
                    "round": 0,
                    "refs": [ref(r) for r in visible],
                    "exposed_fields": {ref(r): list(r) for r in visible},
                    "bytes": len(json.dumps(visible).encode()),
                    "primitive_operations": 0,
                }
            )
        return await super().setup_state(state)

    def update_tool_args(self, tool_name, tool_args, messages, state, **kwargs):
        store = state["fhir_store"]
        store.active_tool = tool_name
        store.agent_tools.append(tool_name)
        store.round = (
            sum(
                (m.get("role") if isinstance(m, dict) else m.role) == "assistant"
                for m in messages
            )
            - state["fhir_prompt_assistant_turns"]
        )
        tool_args["_store"] = store
        return tool_args

    @vf.stop
    async def no_tools_called(self, state):
        if self.tool_protocol == "native":
            return await super().no_tools_called(state)
        if not state["trajectory"]:
            return False
        last = state["trajectory"][-1]["completion"][-1]
        text = last.get("content", "") if isinstance(last, dict) else last.content
        return is_final(text, allow_fence=self.answer_mode == "json_or_fence")

    async def env_response(self, messages, state, **kwargs):
        if self.tool_protocol == "native":
            return await super().env_response(messages, state, **kwargs)
        last = messages[-1]
        text = last.get("content", "") if isinstance(last, dict) else last.content
        try:
            action = parse_object(text)
            if set(action) != {"tool", "args"}:
                raise ValueError(
                    'Use exactly {"tool":"NAME","args":{...}} or the final answer/evidence object'
                )
            name, arguments = action["tool"], action["args"]
            if not isinstance(name, str) or name not in self.action_schemas:
                raise ValueError("Unknown tool name")
            validate(arguments, self.action_schemas[name])
        except (ValueError, TypeError, ValidationError) as exc:
            state["action_format_errors"] += 1
            return [
                vf.UserMessage(
                    content=json.dumps(
                        {
                            "action_error": str(exc).splitlines()[0][:500],
                            "instruction": "Emit one JSON action object with argument values, or a final answer/evidence object. Nothing was executed.",
                        }
                    )
                )
            ]
        arguments = self.update_tool_args(name, arguments, messages, state)
        try:
            observation = await self.call_tool(name, arguments, "json-action")
            content = observation.content
        except (ValueError, TypeError, KeyError) as exc:
            content = json.dumps({"action_error": str(exc)[:500]})
        return [
            vf.UserMessage(content="Tool observation for " + name + ":\n" + content)
        ]


def context_resources(resources, patient_id):
    return [r for r in resources if patient_ref(r) == "Patient/" + patient_id]


def load_environment(
    data_dir: str | None = None,
    split: str = "train",
    eval_split: str = "dev",
    query_mode: str = "rest",
    max_turns: int = 20,
    efficiency_weight: float = 0.0,
    family: str | None = None,
    max_depth: int = 5,
    domains: list[str] | None = None,
    roles: list[str] | None = None,
    crud: str | None = None,
    pilot: bool = False,
    tool_profile: str = "raw",
    families: list[str] | None = None,
    max_examples: int | None = None,
    tool_protocol: str = "native",
    answer_mode: str = "strict",
    retrieval_weight: float = 0.0,
    demonstrations: bool = False,
    discovery_variants: bool = False,
    **kwargs,
) -> vf.Environment:
    """Load the pilot or compiled full dataset. Use dev for tuning and freeze
    before public/heldout evaluation. Hybrid adds experimental SQL read access.
    """
    if crud is not None and (not crud or set(crud) - set("CRUD")):
        raise ValueError("crud must contain only C, R, U, D")
    if query_mode not in {"rest", "hybrid"}:
        raise ValueError("query_mode must be rest or hybrid")
    if tool_profile not in {"raw", "assisted", "none"}:
        raise ValueError("tool_profile must be raw, assisted or none")
    if tool_protocol not in {"native", "json_action"}:
        raise ValueError("tool_protocol must be native or json_action")
    if answer_mode not in {"strict", "json_or_fence"}:
        raise ValueError("answer_mode must be strict or json_or_fence")
    if not 0 <= retrieval_weight <= 0.2:
        raise ValueError("retrieval_weight must be in [0,0.2]")
    if tool_profile == "none" and (
        tool_protocol != "native" or retrieval_weight or demonstrations
    ):
        raise ValueError(
            "No-tools control does not support action transport, retrieval shaping or tool demonstrations"
        )
    if tool_profile == "none":
        allowed = {"latest_result", "coverage_lookup", "longitudinal_lab_trend"}
        if (
            query_mode != "rest"
            or (family and family not in allowed)
            or (families and set(families) - allowed)
        ):
            raise ValueError(
                "No-tools control supports only the three documented read-only families, with query_mode=rest"
            )
        families = families or sorted(allowed)
    if max_examples is not None and max_examples < 1:
        raise ValueError("max_examples must be positive")
    if not 0 <= efficiency_weight <= 0.15:
        raise ValueError("efficiency_weight must be in [0,0.15]")
    packaged = Path(__file__).parent / "training_data"
    root = (
        Path(data_dir)
        if data_dir
        else packaged
        if packaged.exists() and not pilot
        else Path(__file__).parent / "pilot"
    )
    index = {}

    def rows(which):
        path = root / which / "tasks.jsonl"
        if not path.is_file():
            raise ValueError(f"Missing compiled split {which!r}: {path}")
        candidates = load_jsonl(path)
        if discovery_variants:
            from .discovery import add_discovery_variants

            candidates = add_discovery_variants(
                candidates, lambda t: read_shard(str(root / t["shard"]))
            )
        tasks = [
            t
            for t in candidates
            if t["dependency_depth"] <= max_depth
            and (family is None or t["family"] == family)
            and (families is None or t["family"] in families)
            and (domains is None or t.get("domain", "legacy") in domains)
            and (roles is None or t.get("role", "legacy") in roles)
            and (
                crud is None
                or all(
                    {
                        "C": bool(t["gold"]["creates"]),
                        "R": True,
                        "U": bool(t["gold"]["updates"]),
                        "D": bool(t["gold"]["deletes"]),
                    }[operation]
                    for operation in crud
                )
            )
        ]
        if max_examples is not None:
            # Deterministic hash order avoids selecting only the first patient/family.
            tasks = sorted(tasks, key=lambda t: t["id"])[:max_examples]
        if not tasks:
            raise ValueError(f"No tasks match filters in {which}")
        index.update({t["id"]: t for t in tasks})

        def question(task):
            if tool_profile != "none":
                return task["prompt"]
            omitted = set(task["omit"])
            resources = [
                r
                for r in read_shard(str(root / task["shard"]))
                if ref(r) not in omitted
            ]
            return (
                task["prompt"]
                + "\nVisible synthetic patient chart (pre-supplied no-tools control):\n"
                + json.dumps(
                    context_resources(resources, task["patient_id"]), ensure_ascii=False
                )
            )

        return Dataset.from_list(
            [
                {
                    "question": question(t),
                    "answer": "",
                    "info": {
                        "task_id": t["id"],
                        "family": t["family"],
                        "dependency_depth": t["dependency_depth"],
                        "patient_id": t["patient_id"],
                        "split": t["split"],
                        "domain": t.get("domain", "legacy"),
                        "role": t.get("role", "unspecified"),
                        "information_regime": t.get("information_regime", "baseline"),
                        **task_dimensions(t),
                    },
                }
                for t in tasks
            ]
        )

    train = rows(split)
    evaluation = rows(eval_split)

    async def workflow_reward(completion, state, **_):
        metrics = evaluate_training(
            state["fhir_task"],
            state["fhir_store"],
            final_text(completion),
            efficiency_weight,
            answer_mode,
            retrieval_weight,
        )
        metrics["action_format_errors"] = state.get("action_format_errors", 0)
        metrics.update(rollout_usage_metrics(state.get("trajectory", [])))
        state["fhir_metrics"] = metrics
        state["fhir_trace"] = state["fhir_store"].events
        state["fhir_assistance"] = state["fhir_store"].assistance
        return metrics["reward"]

    names = [
        "strict_success",
        "workflow_success",
        "canonical_success",
        "json_format_valid",
        "retrieval_progress",
        "retrieval_shaping",
        "mutation_integrity",
        "action_format_errors",
        "state_success",
        "answer_success",
        "evidence_recall",
        "evidence_precision",
        "tool_calls",
        "agent_tool_calls",
        "helper_calls",
        "helper_response_bytes",
        "context_bytes",
        "primitive_operations",
        "response_bytes",
        "tool_errors",
        "unsafe_attempts",
        "first_evidence_censored",
        "all_evidence_censored",
        "first_evidence_applicable",
        "first_evidence_call_observed",
        "first_evidence_round_observed",
        "all_evidence_call_observed",
        "all_evidence_round_observed",
        "retrieval_hops",
        "workflow_depth",
        "required_evidence_resources",
        "read_calls",
        "write_calls",
        "committed_mutations",
        "unique_read_resources",
        "non_gold_read_resources",
        "cross_patient_read_resources",
        "search_calls",
        "search_rounds",
        "first_action_targeted_search",
        "first_action_broad_search",
        "first_action_document_read",
        "first_turn_targeted_search",
        "first_search_gold_resource_hit",
        "first_search_required_fields_hit",
        "first_turn_gold_resource_hit",
        "first_turn_required_fields_hit",
        "unique_resources_per_search_mean",
        "backend_resources_per_search_mean",
        "gold_resource_search_hit_rate",
        "repeated_queries",
        "distinct_filter_terms",
        "novel_filter_terms",
        "filter_novelty_observed",
        "model_turns",
        "token_usage_complete",
        "token_usage_call_coverage",
        "model_input_tokens",
        "model_output_tokens",
        "model_total_tokens",
        "observed_model_input_tokens",
        "observed_model_output_tokens",
    ]
    names += [
        f"query_{feature}_{stat}"
        for feature in (
            "characters",
            "parameter_count",
            "predicate_count",
            "chain_depth",
            "includes",
            "joins",
        )
        for stat in ("mean", "max")
    ]

    def metric(name):
        async def monitor(completion, state, **_):
            metrics = state.get("fhir_metrics") or evaluate_training(
                state["fhir_task"],
                state["fhir_store"],
                final_text(completion),
                efficiency_weight,
                answer_mode,
                retrieval_weight,
            )
            metrics.update(rollout_usage_metrics(state.get("trajectory", [])))
            return metrics.get(name, state.get(name, 0))

        monitor.__name__ = name
        return monitor

    funcs = [workflow_reward] + [metric(name) for name in names]
    prompt = SYSTEM_PROMPT
    if tool_profile == "none":
        prompt = "Answer the read-only synthetic chart question from the supplied visible FHIR chart. No tools are available. Cite Type/id references from that chart and return exactly the specified answer/evidence JSON. Do not infer undocumented facts. This control measures answer extraction with supplied context, not search efficiency or CRUD execution."
    if tool_profile == "assisted":
        prompt += "\nOptional helpers search visible data and never supply hidden answers. find_patient returns all matches; resolve ambiguity before writes. chart_timeline is a bounded index, read_document decodes inline notes, fhir_schema describes fields, prepare_update prepares a PUT but never commits it. Submit prepared writes using fhir_request. Underlying helper FHIR calls count toward the same request budget."
    if query_mode == "hybrid":
        prompt += (
            "\nExperimental SQLite projection tables/columns: "
            + json.dumps(SCHEMA)
            + "\nThis is not a full SQL on FHIR ViewDefinition runner. Missing-document tasks currently require the same scoped REST search even with SQL enabled."
        )
    if tool_protocol == "json_action":
        prompt += ACTION_INSTRUCTIONS
        if tool_profile == "assisted":
            signatures = [
                helper.__name__
                + str(
                    inspect.signature(helper).replace(
                        parameters=[
                            p
                            for key, p in inspect.signature(helper).parameters.items()
                            if key != "_store"
                        ]
                    )
                )
                for helper in HELPERS
            ]
            prompt += (
                "\nTool signatures: "
                + "; ".join(signatures)
                + ". Use these named arguments in the action's args object."
            )
        if query_mode == "hybrid":
            prompt += "\nTool signature: sql_query(sql)."
    if demonstrations:
        kwargs["few_shot"] = tool_demonstration(tool_protocol)
    return FhirWorkflowEnv(
        root,
        index,
        query_mode,
        tool_profile,
        efficiency_weight,
        tool_protocol,
        answer_mode,
        dataset=train,
        eval_dataset=evaluation,
        system_prompt=prompt,
        rubric=vf.Rubric(funcs=funcs, weights=[1.0] + [0.0] * len(names)),
        max_turns=max_turns,
        **kwargs,
    )


def tool_demonstration(protocol):
    """A public, fictional worked example, never observed in episode state."""
    examples = [
        (
            "Patient?identifier=https://fhir-workflows.example/mrn|EXAMPLE-MRN&_count=5",
            {
                "resourceType": "Patient",
                "id": "demo-patient",
                "identifier": [
                    {
                        "system": "https://fhir-workflows.example/mrn",
                        "value": "EXAMPLE-MRN",
                    }
                ],
            },
        ),
        (
            "Coverage?patient=demo-patient&status=active&_count=5",
            {
                "resourceType": "Coverage",
                "id": "demo-coverage",
                "status": "active",
                "beneficiary": {"reference": "Patient/demo-patient"},
                "payor": [{"reference": "Organization/demo"}],
                "subscriberId": "DEMO-ONLY-123",
            },
        ),
    ]
    messages = [
        vf.UserMessage(
            content="Worked example only: for EXAMPLE-MRN, verify the patient identity, find active coverage, and return subscriber_id and status."
        )
    ]
    for i, (path, resource) in enumerate(examples):
        arguments = {"method": "GET", "path": path, "body_json": "", "headers_json": ""}
        result = json.dumps(
            {
                "status": 200,
                "headers": {},
                "body": {
                    "resourceType": "Bundle",
                    "type": "searchset",
                    "total": 1,
                    "entry": [{"resource": resource}],
                },
            }
        )
        if protocol == "native":
            messages.extend(
                [
                    vf.AssistantMessage(
                        content="",
                        tool_calls=[
                            vf.ToolCall(
                                id=f"demo-call-{i}",
                                name="fhir_request",
                                arguments=json.dumps(arguments),
                            )
                        ],
                    ),
                    vf.ToolMessage(content=result, tool_call_id=f"demo-call-{i}"),
                ]
            )
        else:
            messages.extend(
                [
                    vf.AssistantMessage(
                        content=json.dumps({"tool": "fhir_request", "args": arguments})
                    ),
                    vf.UserMessage(
                        content="Tool observation for fhir_request:\n" + result
                    ),
                ]
            )
    messages.append(
        vf.AssistantMessage(
            content=json.dumps(
                {
                    "answer": {"subscriber_id": "DEMO-ONLY-123", "status": "active"},
                    "evidence": ["Patient/demo-patient", "Coverage/demo-coverage"],
                }
            )
        )
    )
    messages.append(
        vf.UserMessage(
            content="Worked example only: for known Patient/demo-patient, return the latest final hemoglobin (LOINC 718-7) as of 2025-04-04, with value and unit."
        )
    )
    arguments = {
        "method": "GET",
        "path": "Observation?patient=demo-patient&code=http://loinc.org|718-7&status=final&date=le2025-04-04&_sort=-date&_count=1",
        "body_json": "",
        "headers_json": "",
    }
    result = json.dumps(
        {
            "status": 200,
            "headers": {},
            "body": {
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 1,
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Observation",
                            "id": "demo-observation",
                            "status": "final",
                            "code": {
                                "coding": [
                                    {"system": "http://loinc.org", "code": "718-7"}
                                ]
                            },
                            "subject": {"reference": "Patient/demo-patient"},
                            "effectiveDateTime": "2025-03-30",
                            "valueQuantity": {"value": 12.3, "unit": "g/dL"},
                        }
                    }
                ],
            },
        }
    )
    if protocol == "native":
        messages.extend(
            [
                vf.AssistantMessage(
                    content="",
                    tool_calls=[
                        vf.ToolCall(
                            id="demo-lab",
                            name="fhir_request",
                            arguments=json.dumps(arguments),
                        )
                    ],
                ),
                vf.ToolMessage(content=result, tool_call_id="demo-lab"),
            ]
        )
    else:
        messages.extend(
            [
                vf.AssistantMessage(
                    content=json.dumps({"tool": "fhir_request", "args": arguments})
                ),
                vf.UserMessage(content="Tool observation for fhir_request:\n" + result),
            ]
        )
    messages.append(
        vf.AssistantMessage(
            content=json.dumps(
                {
                    "answer": {"value": 12.3, "unit": "g/dL"},
                    "evidence": ["Observation/demo-observation"],
                }
            )
        )
    )
    # Dataset.map stores prompts in Arrow before normalizing them to vf messages.
    return [message.model_dump(exclude_none=True) for message in messages]
