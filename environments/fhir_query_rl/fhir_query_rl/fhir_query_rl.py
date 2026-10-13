"""Native Verifiers v1 synthetic FHIR evaluation taskset and MCP tools."""

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import verifiers.v1 as vf
from fhir_locked_harness import FhirLockedHarnessConfig
from pydantic import Field
from verifiers.v1.envs.single_agent import SingleAgentEnvConfig

from .dataset import load_jsonl
from .helpers import HELPERS
from .metrics import rollout_usage_metrics, task_dimensions
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
        raise TypeError("headers_json must be an object")
    return json.dumps(_store.request(method, path, body, headers), ensure_ascii=False)


async def sql_query(sql: str, _store=None) -> str:
    """SELECT from documented experimental SQLite projections. Return resource_ref
    for each cited resource, with the evidence fields needed by the task. Explicit
    columns only, unique aliases, 50 returned rows maximum. SQL cannot write.

    Args:
        sql: One bounded SQLite SELECT statement over the documented tables.
    """
    return json.dumps(query(_store, sql), ensure_ascii=False)


def context_resources(resources, patient_id):
    return [
        resource
        for resource in resources
        if patient_ref(resource) == "Patient/" + patient_id
    ]


def _load_data(
    data_dir: str | None = None,
    split: str = "dev",
    query_mode: str = "rest",
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
    answer_mode: str = "strict",
    retrieval_weight: float = 0.0,
    discovery_variants: bool = False,
):
    """Load the pilot or compiled full dataset. Use dev for tuning and freeze
    before public/heldout evaluation. Hybrid adds experimental SQL read access.
    """
    if crud is not None and (not crud or set(crud) - set("CRUD")):
        raise ValueError("crud must contain only C, R, U, D")
    if query_mode not in {"rest", "hybrid"}:
        raise ValueError("query_mode must be rest or hybrid")
    if tool_profile not in {"raw", "assisted", "none"}:
        raise ValueError("tool_profile must be raw, assisted or none")
    if answer_mode not in {"strict", "json_or_fence"}:
        raise ValueError("answer_mode must be strict or json_or_fence")
    if not 0 <= retrieval_weight <= 0.2:
        raise ValueError("retrieval_weight must be in [0,0.2]")
    if tool_profile == "none" and retrieval_weight:
        raise ValueError("No-tools control does not support retrieval shaping")
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

        return [
            {
                "prompt": question(t),
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

    selected = rows(split)
    return root, index, selected


class FhirState(vf.State):
    snapshot: dict = Field(default_factory=dict)
    round: int = 0


class FhirTaskData(vf.TaskData):
    case: dict
    info: dict
    data_dir: str


class FhirTaskConfig(vf.TaskConfig):
    query_mode: Literal["rest", "hybrid"] = "rest"
    tool_profile: Literal["raw", "assisted", "none"] = "raw"
    efficiency_weight: float = Field(0.0, ge=0, le=0.15)
    retrieval_weight: float = Field(0.0, ge=0, le=0.2)
    answer_mode: Literal["strict", "json_or_fence"] = "strict"
    max_calls: int = Field(40, ge=1)


class FhirToolConfig(vf.ToolsetConfig):
    colocated: bool = True
    query_mode: str = "rest"
    tool_profile: str = "raw"


class FhirToolset(vf.Toolset[FhirToolConfig, FhirState]):
    TOOL_PREFIX = None

    def register(self, mcp):
        from verifiers.v1.utils.decorators import discover_decorated

        allowed = {"fhir_request"}
        if self.config.query_mode == "hybrid":
            allowed.add("sql_query")
        if self.config.tool_profile == "assisted":
            allowed.update(helper.__name__ for helper in HELPERS)
        if self.config.tool_profile == "none":
            allowed.clear()
        for fn in discover_decorated(self, "tool"):
            if fn.__name__ in allowed:
                mcp.add_tool(
                    self._with_state(fn), name=fn.__name__, description=fn.__doc__
                )

    async def execute(self, name, arguments):
        store = FhirStore.from_snapshot(self.state.snapshot)
        store.active_tool = name
        store.agent_tools.append(name)
        store.round = self.state.round
        functions = {
            "fhir_request": fhir_request,
            "sql_query": sql_query,
            **{h.__name__: h for h in HELPERS},
        }
        try:
            return await functions[name](**arguments, _store=store)
        finally:
            self.state.snapshot = store.snapshot()

    @vf.tool
    async def fhir_request(
        self, method: str, path: str, body_json: str = "", headers_json: str = ""
    ) -> str:
        """Execute bounded FHIR GET/POST/PUT/DELETE; use current If-Match for writes."""
        return await self.execute(
            "fhir_request",
            {
                "method": method,
                "path": path,
                "body_json": body_json,
                "headers_json": headers_json,
            },
        )

    @vf.tool
    async def sql_query(self, sql: str) -> str:
        """Read the experimental SQLite FHIR projection with a bounded SELECT."""
        return await self.execute("sql_query", {"sql": sql})

    @vf.tool
    async def find_patient(
        self, identifier: str = "", name: str = "", birthdate: str = ""
    ) -> str:
        """Search patient candidates using supplied identity fields; preserve ambiguity."""
        return await self.execute(
            "find_patient",
            {"identifier": identifier, "name": name, "birthdate": birthdate},
        )

    @vf.tool
    async def fhir_schema(self, resource_type: str) -> str:
        """Inspect supported FHIR fields and search parameters."""
        return await self.execute("fhir_schema", {"resource_type": resource_type})

    @vf.tool
    async def read_document(
        self, reference: str, start: int = 0, max_characters: int = 6000
    ) -> str:
        """Read bounded inline DocumentReference text."""
        return await self.execute(
            "read_document",
            {"reference": reference, "start": start, "max_characters": max_characters},
        )

    @vf.tool
    async def chart_timeline(
        self,
        patient_id: str,
        resource_types: str = "Encounter,Observation,Communication,DocumentReference",
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        """Read a bounded timeline of patient resources."""
        return await self.execute(
            "chart_timeline",
            {
                "patient_id": patient_id,
                "resource_types": resource_types,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    @vf.tool
    async def prepare_update(self, reference: str, changes_json: str) -> str:
        """Prepare an update without committing it; preserve unrelated fields."""
        return await self.execute(
            "prepare_update", {"reference": reference, "changes_json": changes_json}
        )


class FhirTask(vf.Task[FhirTaskData, FhirState, FhirTaskConfig]):
    @property
    def key(self):
        return self.data.info["task_id"]

    @classmethod
    def toolsets(cls, config):
        if config.tool_profile == "none":
            return []
        return [
            FhirToolset(
                FhirToolConfig(
                    query_mode=config.query_mode, tool_profile=config.tool_profile
                )
            )
        ]

    async def setup(self, trace, runtime):
        task = self.data.case
        resources = [
            copy.deepcopy(r)
            for r in read_shard(str(Path(self.data.data_dir) / task["shard"]))
            if ref(r) not in set(task["omit"])
        ]
        store = FhirStore(
            resources,
            writable_patient="Patient/" + task["patient_id"],
            writable_types=task["writable_types"],
            delete_refs=task["delete_refs"],
            writable_refs=task.get("writable_refs", ()),
            max_calls=self.config.max_calls,
        )
        store.observe_response_terms(self.data.prompt_text)
        store.observe_response_terms(self.data.system_prompt or "")
        if self.config.tool_profile == "none":
            visible = context_resources(resources, task["patient_id"])
            for resource in visible:
                store.observe(resource)
            store.events.append(
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
        trace.state.snapshot = store.snapshot()
        trace.info.update(self.data.info)
        trace.info["evaluation_config"] = self.config.model_dump(mode="json")

    @vf.stop
    def synchronize_round(self, trace: vf.Trace):
        # Trace stop predicates run before the next intercepted model call.
        trace.state.round = len(trace.calls) + 1
        return False

    async def finalize(self, trace, runtime):
        store = FhirStore.from_snapshot(trace.state.snapshot)
        trace.info.update(
            fhir_trace=copy.deepcopy(store.events),
            fhir_assistance=copy.deepcopy(store.assistance),
        )

    def evaluation_metrics(self, trace):
        store = FhirStore.from_snapshot(trace.state.snapshot)
        metrics = evaluate_training(
            self.data.case,
            store,
            trace.last_reply,
            self.config.efficiency_weight,
            self.config.answer_mode,
            self.config.retrieval_weight,
        )
        # v1 prompt_tokens excludes cache reads; input_tokens adds them back.
        metrics.update(
            rollout_usage_metrics(
                [
                    {
                        "response": {
                            "usage": {
                                "prompt_tokens": c.usage.input_tokens,
                                "completion_tokens": c.usage.completion_tokens,
                            }
                            if c.usage
                            else None
                        }
                    }
                    for c in trace.calls
                ]
            )
        )
        trace.info.update(
            fhir_trace=copy.deepcopy(store.events),
            fhir_assistance=copy.deepcopy(store.assistance),
        )
        return metrics

    @vf.metric
    async def workflow_metrics(self, trace, runtime):
        # The current chart/audit lives in runtime state, which is intentionally
        # absent from saved traces; offline replay must not infer chart changes.
        return self.evaluation_metrics(trace)

    @vf.reward
    async def workflow_reward(self, trace, runtime):
        return self.evaluation_metrics(trace)["reward"]


class FhirTasksetConfig(vf.TasksetConfig):
    task: FhirTaskConfig = FhirTaskConfig()
    data_dir: str | None = None
    split: str = "dev"
    family: str | None = None
    families: list[str] | None = None
    max_depth: int = 5
    domains: list[str] | None = None
    roles: list[str] | None = None
    crud: str | None = None
    pilot: bool = False
    max_examples: int | None = None
    discovery_variants: bool = False


class FhirTaskset(vf.Taskset[FhirTask, FhirTasksetConfig]):
    def load(self):
        config = self.config
        options = config.model_dump(exclude={"id", "task", "system_prompt"})
        root, index, rows = _load_data(
            **options,
            **config.task.model_dump(
                exclude={"judges", "stops", "metrics", "rewards", "max_calls"}
            ),
        )
        tool_profile, query_mode = config.task.tool_profile, config.task.query_mode
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
        for row in rows:
            case = index[row["info"]["task_id"]]
            yield FhirTask(
                FhirTaskData(
                    name=case["id"],
                    prompt=row["prompt"],
                    system_prompt=prompt,
                    case=case,
                    info=row["info"],
                    data_dir=str(root.resolve()),
                ),
                config.task,
            )


class FhirEnvConfig(SingleAgentEnvConfig):
    taskset: FhirTasksetConfig = FhirTasksetConfig(id="fhir-query-rl")
    agent: vf.AgentConfig = vf.AgentConfig(
        harness=FhirLockedHarnessConfig(),
        runtime=vf.SubprocessConfig(),
        max_turns=20,
    )


class FhirEnv(vf.Env[FhirEnvConfig]):
    async def run(self, task, agents):
        await agents.agent.run(task)


def load_environment(*, max_turns=20, **kwargs):
    """Construct the native v1 evaluation environment; CLI uses FhirTaskset."""
    task_keys = set(FhirTaskConfig.model_fields) - {
        "judges",
        "stops",
        "metrics",
        "rewards",
    }
    scoring = {k: kwargs.pop(k) for k in list(kwargs) if k in task_keys}
    unknown = set(kwargs) - set(FhirTasksetConfig.model_fields)
    if unknown:
        raise TypeError("Unknown native v1 settings: " + ", ".join(sorted(unknown)))
    config = FhirTasksetConfig(
        id="fhir-query-rl", task=FhirTaskConfig(**scoring), **kwargs
    )
    env = FhirEnv(
        FhirEnvConfig(
            taskset=config,
            agent=vf.AgentConfig(
                harness=FhirLockedHarnessConfig(),
                runtime=vf.SubprocessConfig(),
                max_turns=max_turns,
            ),
        )
    )
    # Materialize once to reject invalid dataset/filter configurations immediately.
    list(env.taskset)
    return env


if __name__ == "__main__":
    FhirToolset.run()
