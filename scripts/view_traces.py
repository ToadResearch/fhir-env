#!/usr/bin/env python3
"""Offline terminal inspector for Verifiers v1 Episode records (traces.jsonl)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_EPISODES = 100_000
MAX_RUNS = 500


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
    paths: list[Path] = []
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
            for root in roots:
                candidate = root / "traces.jsonl"
                if candidate.is_file():
                    paths.append(candidate)
                    if len(paths) >= MAX_RUNS:
                        return [load_run(path) for path in paths]
            if campaign:
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
    return [load_run(path) for path in paths]


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


def make_app(runs: list[Run]):
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import (
        DataTable,
        Footer,
        Header,
        Static,
        TabbedContent,
        TabPane,
    )

    class TraceViewer(App):
        TITLE = "FHIR native trace inspector"
        CSS = """#runs {height: 7;} #tasks {height: 10;} #status {height: 3;} TabbedContent {height: 1fr;} VerticalScroll {height: 1fr;} Static {width: 1fr;}"""
        BINDINGS: ClassVar = [
            ("q", "quit", "Quit"),
            ("r", "focus_runs", "Runs"),
            ("t", "focus_tasks", "Tasks"),
        ]
        selected_run = 0

        def compose(self) -> ComposeResult:
            yield Header()
            yield DataTable(id="runs", cursor_type="row")
            yield DataTable(id="tasks", cursor_type="row")
            yield Static(id="status", markup=False)
            with TabbedContent():
                for i, title in enumerate(
                    [
                        "Question",
                        "Final answer",
                        "Conversation",
                        "Calls and tools",
                        "FHIR trace",
                        "Metrics and errors",
                        "Task data",
                        "Run config",
                    ]
                ):
                    with TabPane(title, id=f"panel-{i}"), VerticalScroll():
                        yield Static(id=f"content-{i}", markup=False)
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#runs", DataTable)
            table.add_columns("Run path", "Episodes", "Load warnings")
            self.query_one("#tasks", DataTable).add_columns(
                "Task / episode", "Recorded scores"
            )
            for i, run in enumerate(runs):
                table.add_row(
                    str(run.path.parent),
                    str(len(run.episodes)),
                    str(len(run.warnings)),
                    key=str(i),
                )
            if runs:
                self.select_run(0)
            else:
                self.query_one("#status", Static).update(
                    "No native traces.jsonl found in the selected output directory."
                )

        def select_run(self, index: int) -> None:
            self.selected_run = index
            run = runs[index]
            table = self.query_one("#tasks", DataTable)
            table.clear()
            for i, episode in enumerate(run.episodes):
                table.add_row(
                    str(episode["task"].get("key") or episode.get("id", i)),
                    trace_summary(episode),
                    key=str(i),
                )
            self.query_one("#status", Static).update(
                "\n".join(run.warnings)
                or f"{len(run.episodes)} episodes loaded from {run.path}"
            )
            for i in range(7):
                self.query_one(f"#content-{i}", Static).update("No episode recorded.")
            self.query_one("#content-7", Static).update(
                pretty(run.config)
                if run.config is not None
                else "No configs/resolved/eval.json recorded."
            )
            if run.episodes:
                self.select_episode(0)

        def select_episode(self, index: int) -> None:
            sections = episode_sections(runs[self.selected_run].episodes[index])
            for i, content in enumerate(sections.values()):
                self.query_one(f"#content-{i}", Static).update(content)

        def on_data_table_row_highlighted(
            self, event: DataTable.RowHighlighted
        ) -> None:
            if event.row_key.value is None:
                return
            if event.data_table.id == "runs":
                self.select_run(int(event.row_key.value))
            elif event.data_table.id == "tasks":
                self.select_episode(int(event.row_key.value))

        def action_focus_runs(self) -> None:
            self.query_one("#runs").focus()

        def action_focus_tasks(self) -> None:
            self.query_one("#tasks").focus()

    return TraceViewer()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-dir", type=Path, default=Path("outputs/evals"))
    args = parser.parse_args()
    if not args.outputs_dir.is_dir():
        parser.error(f"Outputs directory does not exist: {args.outputs_dir}")
    ensure_viewer_runtime(args.outputs_dir)
    make_app(discover_runs(args.outputs_dir)).run()


if __name__ == "__main__":
    main()
