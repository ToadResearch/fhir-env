#!/usr/bin/env python3
"""Offline terminal inspector for Verifiers v1 Episode records (traces.jsonl)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_EPISODES = 100_000
MAX_RUNS = 500
MAX_LOG_TAIL_BYTES = 24 * 1024


def viewer_interpreters(repo: Path, directory: Path) -> list[Path]:
    """Find existing client environments; never create or install one here."""
    candidates = [repo / ".venv" / "bin" / "python"]
    roots = [repo / "outputs" / "hipergator", directory, *list(directory.parents)[:3]]
    for root in dict.fromkeys(roots):
        if (root / "plan.json").is_file():
            candidates.append(root / "client-venv" / "bin" / "python")
        # Only enumerate immediate campaigns, not their environments or caches.
        try:
            for campaign in sorted(root.iterdir(), reverse=True):
                if (
                    campaign.is_dir()
                    and not campaign.is_symlink()
                    and (campaign / "plan.json").is_file()
                ):
                    candidates.append(campaign / "client-venv" / "bin" / "python")
        except OSError:
            continue
    return list(dict.fromkeys(candidates))


def ensure_viewer_runtime(directory: Path) -> None:
    """Reuse installed Textual or re-exec in a prepared project environment."""
    if importlib.util.find_spec("textual") is not None:
        return
    script = Path(__file__).resolve()
    for python in viewer_interpreters(script.parents[1], directory.resolve()):
        if not python.is_file() or not os.access(python, os.X_OK):
            continue
        try:
            probe = subprocess.run(
                [str(python), "-I", "-c", "import textual.app, textual.widgets"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            print(f"Using viewer environment: {python.parent.parent}", file=sys.stderr)
            os.execv(str(python), [str(python), "-I", str(script), *sys.argv[1:]])
    raise SystemExit(
        "No installed viewer environment found. On HiPerGator, wait for a CPU "
        "preparation job to finish. For local use, run uv sync --locked first. "
        "The viewer does not install dependencies or download models."
    )


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)


def reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


@dataclass
class Run:
    path: Path
    episodes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: Any = None
    campaign: Path | None = None
    status: str = "Recorded traces"
    model: str = "Unknown"
    diagnostics: str = ""
    requested: int | None = None


def read_metadata(path: Path, warnings: list[str]) -> dict:
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_LINE_BYTES:
            raise ValueError("Metadata exceeds safety limit")
        value = json.loads(path.read_text(), parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise TypeError("Metadata must be a JSON object")
        return value
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        warnings.append(f"{path.name}: {error}")
        return {}


def log_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - MAX_LOG_TAIL_BYTES))
            text = stream.read(MAX_LOG_TAIL_BYTES).decode("utf-8", errors="replace")
        return ("[Showing log tail]\n" if size > MAX_LOG_TAIL_BYTES else "") + text
    except OSError as error:
        return str(error)


def load_campaign(campaign: Path, trace: Path | None = None) -> Run:
    run = load_run(trace) if trace is not None else Run(campaign / "evals/traces.jsonl")
    run.campaign = campaign
    plan = read_metadata(campaign / "plan.json", run.warnings)
    receipts = {
        name: read_metadata(campaign / "receipts" / f"{name}.json", run.warnings)
        for name in ("jobs", "prepared", "prepare-failed", "gpu", "finished")
    }
    profile = plan.get("profile", {})
    requested = plan.get("num_examples")
    run.requested = requested if isinstance(requested, int) and requested > 0 else None
    run.model = (
        str(profile.get("model", plan.get("preset", "Unknown")))
        if isinstance(profile, dict)
        else "Unknown"
    )
    if run.config is None:
        run.config = {
            "note": "Submission plan; no resolved evaluation config yet",
            "plan": plan,
        }
    finished, gpu = receipts["finished"], receipts["gpu"]
    if finished.get("status") == "completed":
        run.status = "Completed"
    elif finished.get("status") == "failed" or receipts["prepare-failed"]:
        run.status = "Failed"
    elif gpu.get("status") == "completed":
        run.status = "Evaluation completed; reporting pending"
    elif gpu.get("error") or (
        gpu.get("status") == "failed" and "elapsed_seconds" in gpu
    ):
        run.status = "GPU stage failed"
    elif gpu:
        run.status = "GPU stage started; final outcome not recorded"
    elif receipts["prepared"].get("status") == "prepared":
        run.status = "Prepared; no GPU outcome recorded"
    else:
        run.status = "Submitted; no preparation outcome recorded"
    parts = [f"Campaign: {campaign}\nState: {run.status}\nModel: {run.model}"]
    if trace is None:
        parts.append(
            "No evaluation traces have been written. This is not a trace-format error. "
            "Preparation, scheduling or server startup may be incomplete or failed; "
            "inspect the receipts and log tails below."
        )
    parts.append(
        "Recorded receipts (offline; not live Slurm state):\n" + pretty(receipts)
    )
    logs = campaign / "logs"
    for pattern in (
        "prepare-*.log",
        "vllm-cli-preflight.log",
        "gpu-*.log",
        "inference.log",
        "evaluation.log",
        "finish-*.log",
    ):
        paths = sorted(logs.glob(pattern))
        if paths:
            path = paths[-1]
            parts.append(f"--- {path.name} ---\n{log_tail(path)}")
    run.diagnostics = "\n\n".join(parts)
    return run


def load_run(path: Path) -> Run:
    run = Run(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds {MAX_FILE_BYTES} byte safety limit")
        with path.open("rb") as stream:
            line_number = 0
            while line := stream.readline(MAX_LINE_BYTES + 1):
                line_number += 1
                if len(line) > MAX_LINE_BYTES:
                    run.warnings.append(
                        f"Line {line_number}: exceeds line safety limit; loading stopped"
                    )
                    break
                if not line.strip():
                    continue
                try:
                    episode = json.loads(line, parse_constant=reject_constant)
                    if (
                        not isinstance(episode, dict)
                        or not isinstance(episode.get("task"), dict)
                        or not isinstance(episode.get("traces"), list)
                    ):
                        raise TypeError("Expected native Episode with task and traces")
                    for trace in episode["traces"]:
                        if not isinstance(trace, dict) or trace.get("version") != 1:
                            raise ValueError("Only native trace version 1 is supported")
                        if not isinstance(trace.get("nodes", []), list):
                            raise TypeError("Trace nodes must be a list")
                        for name in ("agent", "rewards", "metrics", "info"):
                            if not isinstance(trace.get(name, {}), dict):
                                raise TypeError(f"Trace {name} must be an object")
                        for node in trace.get("nodes", []):
                            if not isinstance(node, dict) or not isinstance(
                                node.get("message"), dict
                            ):
                                raise TypeError(
                                    "Each graph node must contain a message object"
                                )
                        for reward in trace.get("rewards", {}).values():
                            if reward is not None and (
                                not isinstance(reward, dict)
                                or not isinstance(reward.get("score"), (int, float))
                                or not isinstance(reward.get("weight", 1), (int, float))
                            ):
                                raise TypeError(
                                    "Reward must contain numeric score and weight"
                                )
                    run.episodes.append(episode)
                except (TypeError, ValueError, UnicodeError, RecursionError) as error:
                    run.warnings.append(f"Line {line_number}: {error}")
                if len(run.episodes) >= MAX_EPISODES:
                    run.warnings.append("Episode safety limit reached; loading stopped")
                    break
        config_path = path.parent / "configs" / "resolved" / "eval.json"
        if config_path.exists():
            if config_path.stat().st_size > MAX_LINE_BYTES:
                raise ValueError("Resolved config exceeds safety limit")
            run.config = json.loads(
                config_path.read_text(), parse_constant=reject_constant
            )
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        run.warnings.append(str(error))
    return run


def discover_runs(directory: Path) -> list[Run]:
    """Inspect collected runs and Slurm campaigns, without scanning their runtimes."""
    runs: list[Run] = []
    frontier = [directory]
    for _ in range(3):
        children = []
        for folder in sorted(frontier):
            roots = [folder]
            campaign = (folder / "plan.json").is_file()
            if campaign:
                evals = folder / "evals"
                if evals.is_dir() and not evals.is_symlink():
                    roots.append(evals)
                    try:
                        roots.extend(
                            sorted(
                                p
                                for p in evals.iterdir()
                                if p.is_dir() and not p.is_symlink()
                            )
                        )
                    except OSError:
                        pass
            found = False
            for root in roots:
                candidate = root / "traces.jsonl"
                if candidate.is_file():
                    found = True
                    runs.append(
                        load_campaign(folder, candidate)
                        if campaign
                        else load_run(candidate)
                    )
                    if len(runs) >= MAX_RUNS:
                        return runs
            if campaign:
                if not found:
                    runs.append(load_campaign(folder))
                    if len(runs) >= MAX_RUNS:
                        return runs
                # Native results live under evals/PRESET. Do not walk the
                # campaign's client-venv, active-model or preparation artifacts.
                continue
            try:
                children.extend(
                    p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()
                )
            except OSError:
                continue
        frontier = children
    return runs


def trace_summary(episode: dict[str, Any]) -> str:
    parts = [f"episode ok={episode.get('ok', 'missing')}"]
    for trace in episode["traces"]:
        rewards = trace.get("rewards", {})
        scored = [
            r
            for r in rewards.values()
            if isinstance(r, dict) and isinstance(r.get("score"), (int, float))
        ]
        reward = (
            sum(r["score"] * r.get("weight", 1) for r in scored)
            if scored
            else "unscored"
        )
        parts.append(
            f"{trace.get('agent', {}).get('name', 'agent')}: reward={reward}; metrics={json.dumps(trace.get('metrics', {}), ensure_ascii=False)}"
        )
    return " | ".join(parts)


def episode_sections(episode: dict[str, Any]) -> dict[str, str]:
    data = episode["task"].get("data", {})
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if isinstance(prompt, str):
        question = prompt
    elif isinstance(prompt, list):
        blocks = []
        for message in prompt:
            if not isinstance(message, dict):
                blocks.append(pretty(message))
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", pretty(part))
                    if isinstance(part, dict)
                    else str(part)
                    for part in content
                )
            blocks.append(f"[{message.get('role', 'unknown')}]\n{content or ''}")
        question = "\n\n".join(blocks)
    else:
        question = "No prompt recorded. Inspect Task data for the saved task fields."
    sections = {"Question": question}
    finals = []
    conversation = []
    calls = []
    fhir = []
    for index, trace in enumerate(episode["traces"]):
        label = f"Trace {index + 1}: {trace.get('agent', {}).get('name', 'agent')}"
        nodes = trace.get("nodes", [])
        replies = [
            n.get("message", {}).get("content")
            for n in nodes
            if isinstance(n, dict)
            and n.get("sampled")
            and n.get("message", {}).get("role") == "assistant"
        ]
        finals.append(
            label
            + "\n"
            + (
                str(replies[-1])
                if replies and replies[-1] is not None
                else "No sampled assistant text recorded."
            )
        )
        conversation.append(
            label
            + "\n"
            + "\n\n".join(
                f"Node {i} · parent={n.get('parent')} · sampled={n.get('sampled', False)}\n{pretty(n.get('message', {}))}"
                for i, n in enumerate(nodes)
                if isinstance(n, dict)
            )
        )
        calls.append(
            label
            + "\nModel calls\n"
            + pretty(trace.get("calls", []))
            + "\nAdvertised tools\n"
            + pretty(trace.get("tools", []))
        )
        info = trace.get("info", {})
        fhir.append(
            label
            + "\n"
            + pretty(info.get("fhir_trace", "No fhir_trace recorded in trace.info."))
        )
    sections["Final answer"] = "\n\n".join(finals) or "No agent trace recorded."
    sections["Conversation"] = "\n\n".join(conversation) or "No conversation recorded."
    sections["Calls and tools"] = "\n\n".join(calls) or "No calls recorded."
    sections["FHIR trace"] = "\n\n".join(fhir) or "No FHIR evidence recorded."
    sections["Metrics and errors"] = (
        trace_summary(episode)
        + "\n\n"
        + pretty(
            {
                "episode_errors": episode.get("errors", []),
                "traces": [
                    {
                        key: trace.get(key)
                        for key in (
                            "id",
                            "ok",
                            "is_completed",
                            "stop_condition",
                            "rewards",
                            "metrics",
                            "errors",
                            "timing",
                            "info",
                        )
                    }
                    for trace in episode["traces"]
                ],
            }
        )
    )
    sections["Task data"] = pretty(episode["task"])
    return sections


def model_id(run: Run) -> str:
    if run.model != "Unknown":
        return run.model
    if isinstance(run.config, dict) and run.config.get("model"):
        return str(run.config["model"])
    for episode in run.episodes:
        for trace in episode["traces"]:
            for call in trace.get("calls", []):
                if isinstance(call, dict) and call.get("model"):
                    return str(call["model"])
    return "Unknown"


def model_label(model: str) -> str:
    return {
        "openai/gpt-oss-20b": "GPT-OSS 20B",
        "RedHatAI/Muse-Glimmer-30B-FP8-block": "Muse Glimmer",
        "IFM/K2-Horizon-7B-FP8": "K2 Horizon 7B",
        "LiquidAI/LFM2.5-24B-A2B": "Liquid LFM2.5",
    }.get(model, model.rsplit("/", 1)[-1])


def run_time(run: Run) -> float:
    """Submission time first; filesystem mtime only for undated imports."""
    match = re.search(r"(\d{8}-\d{6}Z)", str(run.campaign or run.path.parent))
    if match:
        try:
            return (
                datetime.strptime(match[1], "%Y%m%d-%H%M%SZ")
                .replace(tzinfo=UTC)
                .timestamp()
            )
        except ValueError:
            pass
    try:
        return (run.campaign or run.path).stat().st_mtime
    except OSError:
        return 0


def metric(episode: dict, name: str) -> float | None:
    values = [trace.get("metrics", {}).get(name) for trace in episode["traces"]]
    if not values or any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        return None
    return min(values)


def outcome(episode: dict) -> str:
    """Correctness comes from workflow metrics, never reward or execution-ok."""
    if (
        episode.get("ok") is False
        or episode.get("errors")
        or any(
            trace.get("ok") is False or trace.get("errors")
            for trace in episode["traces"]
        )
    ):
        return "Error"
    successes = []
    for trace in episode["traces"]:
        metrics = trace.get("metrics", {})
        successes.append(metrics.get("workflow_success", metrics.get("strict_success")))
    if not successes or any(value not in (0, 1) for value in successes):
        return "Unscored"
    return "Correct" if all(value == 1 for value in successes) else "Incorrect"


def task_family(episode: dict) -> str:
    data = episode.get("task", {}).get("data", {})
    if not isinstance(data, dict):
        return "Task"
    for source in (data.get("info"), data.get("case"), data):
        if isinstance(source, dict) and source.get("family"):
            return str(source["family"]).replace("_", " ")
    return "Task"


def task_key(episode: dict) -> str:
    return str(episode.get("task", {}).get("key") or episode.get("id", "unnamed"))


def episode_identity(episode: dict) -> str:
    # Several rollouts can share a task key.
    return str(episode.get("id") or task_key(episode))


def usage(episode: dict) -> tuple[int, int | None]:
    calls = [
        call
        for trace in episode["traces"]
        for call in trace.get("calls", [])
        if isinstance(call, dict)
    ]
    counts = [(call.get("usage") or {}).get("completion_tokens") for call in calls]
    tokens = (
        sum(counts)
        if counts and all(isinstance(n, int) and n >= 0 for n in counts)
        else None
    )
    return len(calls), tokens


def run_counts(run: Run) -> Counter:
    return Counter(outcome(episode) for episode in run.episodes)


def score_label(run: Run) -> str:
    counts = run_counts(run)
    total = run.requested or len(run.episodes)
    return f"{counts['Correct']} / {total}" if run.episodes else f"— / {total or '—'}"


def bounded(text: str, expanded: bool = False) -> str:
    limit, lines = (40_000, 400) if expanded else (6_000, 60)
    clipped = "\n".join(text.splitlines()[:lines])[:limit]
    return clipped + (
        "\n… Display shortened; press e to expand. Full data remains in traces.jsonl."
        if clipped != text
        else ""
    )


def message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", f"[{part.get('type', 'content')} attachment]"))
            if isinstance(part, dict)
            else str(part)
            for part in content
        )
    return pretty(content)


def task_prompt(episode: dict) -> str:
    data = episode.get("task", {}).get("data", {})
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, list):
        return "\n\n".join(
            f"[{m.get('role', 'unknown')}]\n{message_text(m.get('content'))}"
            if isinstance(m, dict)
            else str(m)
            for m in prompt
        )
    return "No prompt recorded. Inspect Raw for the saved task fields."


def final_text(episode: dict) -> str:
    finals = []
    for i, trace in enumerate(episode["traces"]):
        replies = [
            n["message"]
            for n in trace.get("nodes", [])
            if n.get("sampled") and n["message"].get("role") == "assistant"
        ]
        text = message_text(replies[-1].get("content")) if replies else ""
        finals.append(
            f"Agent {i + 1} · {trace.get('agent', {}).get('name', 'agent')}\n"
            + (text or "No final assistant text recorded.")
        )
    return "\n\n".join(finals) or "No agent trace recorded."


def rollout_view(episode: dict, reasoning: bool = False, expanded: bool = False):
    """Render native nodes directly; prompt examples are not sampled turns."""
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text

    def body(text, structured=False):
        if structured:
            try:
                text = pretty(json.loads(text, parse_constant=reject_constant))
            except (ValueError, TypeError, RecursionError):
                return Text(bounded(text, expanded))
            return Syntax(
                bounded(text, expanded),
                "json",
                word_wrap=True,
                background_color="default",
            )
        return Markdown(bounded(text, expanded))

    blocks = []
    for trace_index, trace in enumerate(episode["traces"]):
        blocks.append(
            Text(
                f"Agent {trace_index + 1} · {trace.get('agent', {}).get('name', 'agent')}",
                style="bold",
            )
        )
        tools = {}
        turn = 0
        for node in trace.get("nodes", []):
            message = node["message"]
            role = message.get("role")
            if role == "assistant" and node.get("sampled"):
                turn += 1
                if reasoning and message.get("reasoning_content"):
                    blocks.append(
                        Panel(
                            body(message["reasoning_content"]),
                            title=f"Turn {turn} · Reasoning",
                            border_style="dim",
                        )
                    )
                content = message_text(message.get("content"))
                if content:
                    blocks.append(
                        Panel(
                            body(content),
                            title=f"Turn {turn} · Assistant",
                            border_style="cyan",
                        )
                    )
                for call in message.get("tool_calls") or []:
                    name = str(call.get("name", "tool"))
                    tools[call.get("id")] = name
                    args = call.get("arguments", "{}")
                    blocks.append(
                        Panel(
                            body(args if isinstance(args, str) else pretty(args), True),
                            title=Text(f"Turn {turn} · Call {name}"),
                            border_style="blue",
                        )
                    )
            elif role == "tool" and turn:
                name = message.get("name") or tools.get(
                    message.get("tool_call_id"), "tool"
                )
                blocks.append(
                    Panel(
                        body(message_text(message.get("content")), True),
                        title=Text(f"Result · {name}"),
                        border_style="green",
                    )
                )
            elif role == "user" and turn:
                blocks.append(
                    Panel(
                        body(message_text(message.get("content"))),
                        title="User follow-up",
                        border_style="yellow",
                    )
                )
        if not turn:
            blocks.append(Text("No sampled assistant turns recorded.", style="dim"))
    return Group(*blocks) if blocks else Text("No conversation recorded.")


def score_details(episode: dict) -> str:
    labels = {
        "workflow_success": "Workflow correct (configured answer mode)",
        "strict_success": "Strict JSON correct",
        "answer_success": "Answer correct",
        "state_success": "Chart state correct",
        "evidence_precision": "Evidence precision",
        "evidence_recall": "Evidence recall",
        "mutation_integrity": "Mutation integrity",
        "unsafe_attempts": "Unsafe attempts",
    }
    lines = [f"Result: {outcome(episode)}", ""]
    for name, label in labels.items():
        value = metric(episode, name)
        if value is not None:
            lines.append(f"{label}: {value:g}")
    turns, tokens = usage(episode)
    lines.extend(
        [
            "",
            f"Model calls: {turns}",
            f"Output tokens: {tokens if tokens is not None else 'not recorded'}",
        ]
    )
    errors = [episode.get("errors", [])] + [
        t.get("errors", []) for t in episode["traces"]
    ]
    if any(errors):
        lines.extend(["", "Execution errors", bounded(pretty(errors))])
    return "\n".join(lines)


def make_app(runs: list[Run], reload_runs=None):
    from rich.text import Text
    from textual import events
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import (
        DataTable,
        Footer,
        Header,
        Input,
        Select,
        Static,
        TabbedContent,
        TabPane,
    )

    colors = {
        "Correct": "green",
        "Incorrect": "yellow",
        "Error": "red",
        "Unscored": "dim",
    }

    class TraceViewer(App):
        TITLE = "FHIR Query RL · Evaluation browser"
        CSS = """
        Screen {background: $surface;}
        #workspace {height: 1fr;}
        #model-pane {width: 27; border-right: solid $primary-darken-2; padding: 0 1;}
        #main {width: 1fr; padding: 0 1;}
        .label {height: 1; color: $text-muted; text-style: bold; margin-top: 1;}
        #models {height: 1fr;}
        #runs {height: 8;}
        #status {height: 4; padding: 0 1; background: $boost;}
        #body {height: 1fr; margin-top: 1;}
        #task-pane {width: 39; border-right: solid $primary-darken-2; padding-right: 1;}
        #tasks {height: 1fr;}
        Input, Select {height: 3; margin: 0;}
        TabbedContent {width: 1fr; height: 1fr;}
        TabPane {padding: 1;}
        VerticalScroll {height: 1fr;}
        Static {width: 1fr;}
        DataTable {background: $surface;}
        DataTable > .datatable--header {background: $boost; color: $text-muted;}
        DataTable:focus > .datatable--cursor {background: $primary-darken-2; color: $text;}
        .compact #model-pane {display: none;}
        #compact-model {display: none;}
        .compact #compact-model {display: block;}
        .compact #task-pane {width: 32;}
        .short #runs {height: 5;}
        """
        BINDINGS: ClassVar = [
            ("q", "quit", "Quit"),
            ("m", "focus_models", "Models"),
            ("r", "focus_runs", "Runs"),
            ("t", "focus_tasks", "Tasks"),
            ("/", "search", "Search"),
            ("n", "next_task", "Next"),
            ("p", "previous_task", "Previous"),
            ("a", "reasoning", "Reasoning"),
            ("e", "expand", "Expand"),
            ("f", "refresh_runs", "Refresh"),
        ]
        selected_run = 0
        selected_episode = 0
        selected_model = ""
        visible_tasks: ClassVar[list[int]] = []
        reasoning = False
        expanded = False
        updating = False

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="workspace"):
                with Vertical(id="model-pane"):
                    yield Static("MODELS", classes="label")
                    yield DataTable(id="models", cursor_type="row")
                with Vertical(id="main"):
                    yield Select([], prompt="Choose model", id="compact-model")
                    yield Static("RUNS · newest first · UTC", classes="label")
                    yield DataTable(id="runs", cursor_type="row")
                    yield Static(id="status", markup=False)
                    with Horizontal(id="body"):
                        with Vertical(id="task-pane"):
                            yield Input(placeholder="Search tasks…  /", id="search")
                            yield Select(
                                [
                                    (s, s)
                                    for s in (
                                        "All",
                                        "Incorrect",
                                        "Error",
                                        "Correct",
                                        "Unscored",
                                    )
                                ],
                                value="All",
                                allow_blank=False,
                                id="filter",
                            )
                            yield DataTable(id="tasks", cursor_type="row")
                        with TabbedContent():
                            for i, title in enumerate(
                                ("Task", "Rollout", "Scores", "Campaign", "Raw")
                            ):
                                with TabPane(title, id=f"panel-{i}"), VerticalScroll():
                                    yield Static(id=f"content-{i}", markup=False)
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#models", DataTable).add_columns("Model", "Runs")
            self.query_one("#runs", DataTable).add_columns(
                "Started (UTC)", "Run", "State", "Correct / total", "Errors"
            )
            tasks = self.query_one("#tasks", DataTable)
            tasks.add_column("Result", width=9)
            tasks.add_column("Task", width=16)
            tasks.add_column("Calls", width=4)
            self.populate_models()
            self.action_focus_runs()

        def on_resize(self, event: events.Resize) -> None:
            self.set_class(event.size.width < 120, "compact")
            self.set_class(event.size.height < 35, "short")

        def populate_models(self, previous: Path | None = None) -> None:
            self.updating = True
            runs.sort(key=lambda r: (run_time(r), str(r.path)), reverse=True)
            self.summaries = {i: run_counts(run) for i, run in enumerate(runs)}
            self.models = list(dict.fromkeys(model_id(run) for run in runs))
            table = self.query_one("#models", DataTable)
            table.clear()
            for model in self.models:
                table.add_row(
                    Text(model_label(model)),
                    str(sum(model_id(r) == model for r in runs)),
                    key=model,
                )
            selector = self.query_one("#compact-model", Select)
            selector.set_options([(model_label(m), m) for m in self.models])
            if previous:
                match = next(
                    (r for r in runs if (r.campaign or r.path) == previous), None
                )
                if match:
                    self.selected_model = model_id(match)
            if self.selected_model not in self.models:
                self.selected_model = self.models[0] if self.models else ""
            if self.models:
                selector.value = self.selected_model
                table.move_cursor(row=self.models.index(self.selected_model))
            self.updating = False
            self.populate_runs(previous)

        def populate_runs(self, previous: Path | None = None) -> None:
            self.updating = True
            table = self.query_one("#runs", DataTable)
            table.clear()
            self.visible_runs = [
                i for i, run in enumerate(runs) if model_id(run) == self.selected_model
            ]
            for i in self.visible_runs:
                run = runs[i]
                stamp = run_time(run)
                date = (
                    datetime.fromtimestamp(stamp, UTC).strftime("%m-%d %H:%M:%S")
                    if stamp
                    else "Unknown"
                )
                state = (
                    run.status.split(";")[0]
                    .replace("GPU stage", "GPU")
                    .replace("Evaluation completed", "Eval done")
                )
                state_style = (
                    "red"
                    if "failed" in state.lower()
                    else "green"
                    if state == "Completed"
                    else "dim"
                )
                table.add_row(
                    date,
                    Text((run.campaign or run.path.parent).name),
                    Text(state, style=state_style),
                    score_label(run),
                    str(self.summaries[i]["Error"]),
                    key=str(i),
                )
            if self.visible_runs:
                index = next(
                    (
                        i
                        for i in self.visible_runs
                        if (runs[i].campaign or runs[i].path) == previous
                    ),
                    self.visible_runs[0],
                )
                table.move_cursor(row=self.visible_runs.index(index))
                self.select_run(index)
            else:
                self.query_one("#status", Static).update(
                    "No campaigns or native traces found. Submit an evaluation, then press f to refresh."
                )
                for i in range(5):
                    self.query_one(f"#content-{i}", Static).update("No run selected.")
            self.updating = False

        def select_run(self, index: int) -> None:
            changed = self.selected_run != index
            self.selected_run = index
            run = runs[index]
            counts = self.summaries[index]
            total = run.requested or len(run.episodes)
            observed = len(run.episodes)
            scored = counts["Correct"] + counts["Incorrect"]
            rate = (
                f" · {counts['Correct'] / scored:.0%} of scored tasks"
                if scored
                else " · no correctness scores yet"
            )
            turns_tokens = [usage(e) for e in run.episodes]
            known_tokens = [tokens for _, tokens in turns_tokens if tokens is not None]
            cost = (
                f"Avg model calls: {sum(n for n, _ in turns_tokens) / observed:.1f}"
                if observed
                else "No rollouts recorded yet"
            )
            cost += (
                f" · avg output tokens: {sum(known_tokens) / len(known_tokens):,.0f} ({len(known_tokens)}/{observed} recorded)"
                if known_tokens
                else " · token usage not recorded"
            )
            self.query_one("#status", Static).update(
                f"{model_id(run)} · {run.status} · {observed}/{total or '—'} recorded\n"
                f"Correct {counts['Correct']} · incorrect {counts['Incorrect']} · errors {counts['Error']} · unscored {counts['Unscored']}{rate}\n"
                f"{cost} · load warnings: {len(run.warnings)}\n"
                "Correct = workflow_success (strict_success fallback). Missing tasks are not scored."
            )
            if changed:
                self.selected_episode = 0
            self.populate_tasks()
            families = Counter(task_family(e) for e in run.episodes)
            overview = [f"{run.campaign or run.path}", "", "Task families"] + [
                f"{name}: {count}" for name, count in sorted(families.items())
            ]
            overview.extend(
                [
                    "",
                    "Load warnings",
                    "\n".join(run.warnings) or "None",
                    "",
                    run.diagnostics
                    or "No Slurm metadata. See Raw for resolved evaluation config.",
                ]
            )
            self.query_one("#content-3", Static).update("\n".join(overview))

        def populate_tasks(self) -> None:
            if not runs:
                return
            run = runs[self.selected_run]
            query = self.query_one("#search", Input).value.casefold()
            filter_value = self.query_one("#filter", Select).value
            table = self.query_one("#tasks", DataTable)
            table.clear()
            self.visible_tasks = []
            for i, episode in enumerate(run.episodes):
                result = outcome(episode)
                if filter_value != "All" and result != filter_value:
                    continue
                # Search only public prompt/identity, not hidden grading data.
                prompt = task_prompt(episode)
                if (
                    query
                    and query
                    not in (
                        task_key(episode) + task_family(episode) + str(prompt)
                    ).casefold()
                ):
                    continue
                self.visible_tasks.append(i)
                family = task_family(episode)
                label = f"{i + 1}. " + (
                    family if family != "Task" else task_key(episode)[:18]
                )
                table.add_row(
                    Text(result, style=colors[result]),
                    Text(label),
                    str(usage(episode)[0]),
                    key=str(i),
                )
            if self.visible_tasks:
                index = (
                    self.selected_episode
                    if self.selected_episode in self.visible_tasks
                    else self.visible_tasks[0]
                )
                table.move_cursor(row=self.visible_tasks.index(index))
                self.select_episode(index)
            else:
                text = (
                    "No tasks match these filters."
                    if run.episodes
                    else "No rollouts recorded yet. Open Campaign for preparation/GPU receipts and logs."
                )
                for i in (0, 1, 2, 4):
                    self.query_one(f"#content-{i}", Static).update(text)
                self.query_one("#content-4", Static).update(
                    bounded(pretty(run.config), self.expanded)
                )

        def select_episode(self, index: int) -> None:
            self.selected_episode = index
            run = runs[self.selected_run]
            episode = run.episodes[index]
            self.query_one("#content-0", Static).update(
                f"{outcome(episode).upper()} · {task_family(episode)}\nID: {task_key(episode)}\n\n"
                f"REQUEST\n{bounded(task_prompt(episode), self.expanded)}\n\n"
                f"FINAL ANSWER\n{bounded(final_text(episode), self.expanded)}"
            )
            self.query_one("#content-1", Static).update(
                rollout_view(episode, self.reasoning, self.expanded)
            )
            self.query_one("#content-2", Static).update(score_details(episode))
            self.query_one("#content-4", Static).update(
                "Native episode (includes grading data)\n"
                + bounded(pretty(episode), self.expanded)
                + "\n\nResolved evaluation config / submission plan\n"
                + bounded(pretty(run.config), self.expanded)
            )

        def on_data_table_row_highlighted(
            self, event: DataTable.RowHighlighted
        ) -> None:
            if self.updating or event.row_key.value is None:
                return
            key = event.row_key.value
            # Highlight events are queued. Ignore old highlights from a table
            # rebuild rather than undoing the user's preserved selection.
            row = event.data_table.cursor_row
            keys = {
                "models": self.models,
                "runs": [str(i) for i in self.visible_runs],
                "tasks": [str(i) for i in self.visible_tasks],
            }.get(event.data_table.id, [])
            if row >= len(keys) or keys[row] != key:
                return
            if event.data_table.id == "models" and key != self.selected_model:
                self.selected_model = key
                self.query_one("#compact-model", Select).value = key
                self.populate_runs()
            elif event.data_table.id == "runs":
                index = int(key)
                if index in self.visible_runs:
                    self.select_run(index)
            elif event.data_table.id == "tasks":
                index = int(key)
                if index in self.visible_tasks:
                    self.select_episode(index)

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "search" and not self.updating:
                self.populate_tasks()

        def on_select_changed(self, event: Select.Changed) -> None:
            if self.updating or event.value != event.select.value:
                return
            if (
                event.select.id == "compact-model"
                and event.value in self.models
                and event.value != self.selected_model
            ):
                self.selected_model = event.value
                self.query_one("#models", DataTable).move_cursor(
                    row=self.models.index(event.value)
                )
                self.populate_runs()
            elif event.select.id == "filter":
                self.populate_tasks()

        def action_focus_models(self) -> None:
            self.query_one(
                "#compact-model" if self.size.width < 120 else "#models"
            ).focus()

        def action_focus_runs(self) -> None:
            self.query_one("#runs").focus()

        def action_focus_tasks(self) -> None:
            self.query_one("#tasks").focus()

        def action_search(self) -> None:
            self.query_one("#search").focus()

        def action_next_task(self) -> None:
            self.move_task(1)

        def action_previous_task(self) -> None:
            self.move_task(-1)

        def move_task(self, delta: int) -> None:
            if self.visible_tasks:
                row = self.visible_tasks.index(self.selected_episode)
                self.query_one("#tasks", DataTable).move_cursor(
                    row=max(0, min(len(self.visible_tasks) - 1, row + delta))
                )

        def action_reasoning(self) -> None:
            self.reasoning = not self.reasoning
            self.redraw_episode()
            self.notify("Reasoning shown" if self.reasoning else "Reasoning hidden")

        def action_expand(self) -> None:
            self.expanded = not self.expanded
            self.redraw_episode()
            self.notify(
                "Long outputs expanded" if self.expanded else "Long outputs shortened"
            )

        def redraw_episode(self) -> None:
            if runs and self.visible_tasks:
                self.select_episode(self.selected_episode)

        def action_refresh_runs(self) -> None:
            if reload_runs is not None:
                previous = (
                    (runs[self.selected_run].campaign or runs[self.selected_run].path)
                    if runs
                    else None
                )
                selected_key = (
                    episode_identity(
                        runs[self.selected_run].episodes[self.selected_episode]
                    )
                    if runs and runs[self.selected_run].episodes
                    else None
                )
                runs[:] = reload_runs()
                self.populate_models(previous)
                if runs and selected_key:
                    index = next(
                        (
                            i
                            for i, e in enumerate(runs[self.selected_run].episodes)
                            if episode_identity(e) == selected_key
                        ),
                        None,
                    )
                    if index is not None and index in self.visible_tasks:
                        self.selected_episode = index
                        self.query_one("#tasks", DataTable).move_cursor(
                            row=self.visible_tasks.index(index)
                        )
                        self.select_episode(index)
                self.notify("Results refreshed from disk")

    return TraceViewer()


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs",
    )
    return parser


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if not args.outputs_dir.is_dir():
        cli.error(
            f"Outputs directory does not exist: {args.outputs_dir}; submit an evaluation first"
        )
    ensure_viewer_runtime(args.outputs_dir)
    make_app(
        discover_runs(args.outputs_dir), lambda: discover_runs(args.outputs_dir)
    ).run()


if __name__ == "__main__":
    main()
