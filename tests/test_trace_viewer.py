"""Native v1 trace parsing and offline terminal navigation."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "view_traces", Path(__file__).parents[1] / "scripts" / "view_traces.py"
)
viewer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = viewer
spec.loader.exec_module(viewer)


def test_bootstrap_import_does_not_require_datetime_utc():
    # Model the system interpreter before the prepared runtime is selected.
    script = Path(viewer.__file__).resolve()
    code = (
        "import datetime, runpy, sys; "
        "hasattr(datetime, 'UTC') and delattr(datetime, 'UTC'); "
        "module = runpy.run_path(sys.argv[1], run_name='bootstrap_test'); "
        "assert module['UTC'] is datetime.timezone.utc; "
        "assert 'textual' not in sys.modules; "
        "assert module['parser']().parse_args([]).outputs_dir.name == 'outputs'"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def episode(key="task-one"):
    return {
        "id": key,
        "ok": True,
        "task": {"type": "FHIRTask", "key": key, "data": {"prompt": "Find patient"}},
        "traces": [
            {
                "version": 1,
                "agent": {"name": "solver"},
                "nodes": [
                    {
                        "parent": None,
                        "message": {"role": "user", "content": "Find patient"},
                    },
                    {
                        "parent": 0,
                        "sampled": True,
                        "message": {
                            "role": "assistant",
                            "content": "patient-1",
                            "tool_calls": [{"name": "search", "arguments": "{}"}],
                        },
                    },
                    {
                        "parent": 1,
                        "message": {"role": "tool", "content": "tool-result"},
                    },
                ],
                "calls": [{"node": 1, "model": "test-model"}],
                "rewards": {"correctness": {"score": 1, "weight": 0.5}},
                "metrics": {"strict_success": 1},
                "info": {"fhir_trace": [{"operation": "search"}]},
            }
        ],
    }


def write_run(path, episodes):
    path.mkdir(parents=True)
    trace = path / "traces.jsonl"
    trace.write_text("".join(json.dumps(e) + "\n" for e in episodes))
    return trace


def test_default_outputs_are_repository_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert (
        viewer.parser().parse_args([]).outputs_dir
        == Path(viewer.__file__).resolve().parents[1] / "outputs"
    )


def test_failed_campaign_without_rollouts_is_visible(tmp_path):
    campaign = tmp_path / "hipergator" / "gptoss-failed"
    (campaign / "evals").mkdir(parents=True)
    (campaign / "logs").mkdir()
    (campaign / "receipts").mkdir()
    (campaign / "plan.json").write_text(
        json.dumps({"profile": {"model": "openai/gpt-oss-20b"}})
    )
    (campaign / "receipts/finished.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "gpu_status": "missing; inspect CPU/Slurm logs",
                "episodes": 0,
            }
        )
    )
    (campaign / "logs/prepare-45815636.log").write_text(
        "container CLI preflight failed\n"
    )
    runs = viewer.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].campaign == campaign
    assert runs[0].status == "Failed"
    assert runs[0].model == "openai/gpt-oss-20b"
    assert not runs[0].episodes
    assert "container CLI preflight failed" in runs[0].diagnostics
    assert "not a trace-format error" in runs[0].diagnostics


def test_unfinished_campaign_does_not_claim_live_scheduler_state(tmp_path):
    (tmp_path / "plan.json").write_text("{}")
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts/prepared.json").write_text('{"status":"prepared"}')
    run = viewer.discover_runs(tmp_path)[0]
    assert run.status == "Prepared; no GPU outcome recorded"
    assert "not live Slurm state" in run.diagnostics


def test_native_output_contract_is_discoverable(tmp_path):
    from types import SimpleNamespace

    from verifiers.v1.cli.output import output_path

    campaign = tmp_path / "hipergator/campaign"
    campaign.mkdir(parents=True)
    (campaign / "plan.json").write_text('{"preset":"gpt-oss-20b"}')
    native_dir = output_path(
        SimpleNamespace(
            output_dir=campaign / "evals", run=SimpleNamespace(dir="gpt-oss-20b")
        )
    )
    trace = write_run(native_dir, [episode()])
    runs = viewer.discover_runs(tmp_path)
    assert [r.path for r in runs] == [trace]
    assert len(runs[0].episodes) == 1


@pytest.mark.asyncio
async def test_refresh_reveals_first_rollout_for_empty_campaign(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import DataTable, Static

    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "plan.json").write_text('{"preset":"glimmer"}')
    earlier = tmp_path / "a-first-campaign"
    earlier.mkdir()
    (earlier / "plan.json").write_text('{"preset":"gpt-oss-20b"}')
    app = viewer.make_app(
        viewer.discover_runs(tmp_path), lambda: viewer.discover_runs(tmp_path)
    )
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert app.query_one("#models", DataTable).row_count == 2
        app.selected_model = "glimmer"
        app.populate_runs()
        await pilot.pause()
        assert app.selected_model == "glimmer"
        assert app.query_one("#tasks", DataTable).row_count == 0
        assert "No rollouts" in str(app.query_one("#content-0", Static).content)
        write_run(campaign / "evals/glimmer", [episode("first-result")])
        await pilot.press("f")
        await pilot.pause()
        assert app.selected_model == "glimmer"
        assert app.query_one("#tasks", DataTable).row_count == 1
        assert "Find patient" in str(app.query_one("#content-0", Static).content)


def test_viewer_interpreters_find_project_and_selected_external_campaign(tmp_path):
    repo = tmp_path / "repo"
    standard = repo / "outputs" / "hipergator" / "standard-run"
    external = tmp_path / "external-run"
    for campaign in (standard, external):
        (campaign / "evals" / "model").mkdir(parents=True)
        (campaign / "plan.json").write_text("{}")
    candidates = viewer.viewer_interpreters(repo, external / "evals" / "model")
    assert candidates[0] == repo / ".venv" / "bin" / "python"
    assert standard / "client-venv" / "bin" / "python" in candidates
    assert external / "client-venv" / "bin" / "python" in candidates
    assert len(candidates) == len(set(candidates))


def test_viewer_runtime_reuses_current_textual_without_probing(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.importlib.util, "find_spec", lambda name: object())

    def unexpected(*args, **kwargs):
        raise AssertionError("No environment probing is needed")

    monkeypatch.setattr(viewer.subprocess, "run", unexpected)
    viewer.ensure_viewer_runtime(tmp_path)


def test_viewer_runtime_skips_broken_environment_and_reexecs(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    broken = tmp_path / "broken-python"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    working = Path(sys.executable)
    monkeypatch.setattr(viewer.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(viewer, "viewer_interpreters", lambda *args: [broken, working])
    arguments = ["view_traces.py", "--outputs-dir", str(tmp_path)]
    monkeypatch.setattr(viewer.sys, "argv", arguments)
    executed = []

    def capture_exec(python, argv):
        executed.append((python, argv))
        raise SystemExit("reexec")

    monkeypatch.setattr(viewer.os, "execv", capture_exec)
    with pytest.raises(SystemExit, match="reexec"):
        viewer.ensure_viewer_runtime(tmp_path)
    assert executed == [
        (
            str(working),
            [str(working), "-I", str(Path(viewer.__file__).resolve()), *arguments[1:]],
        )
    ]


def test_viewer_runtime_explains_missing_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(viewer, "viewer_interpreters", lambda *args: [])
    with pytest.raises(SystemExit, match="wait for a CPU preparation job"):
        viewer.ensure_viewer_runtime(tmp_path)


def test_native_evidence_and_weighted_reward(tmp_path):
    run = viewer.load_run(write_run(tmp_path / "run", [episode()]))
    assert not run.warnings
    sections = viewer.episode_sections(run.episodes[0])
    assert sections["Question"] == "Find patient"
    assert "patient-1" in sections["Final answer"]
    assert "tool-result" in sections["Conversation"]
    assert "parent=1" in sections["Conversation"]
    assert "test-model" in sections["Calls and tools"]
    assert "search" in sections["FHIR trace"]
    assert "reward=0.5" in sections["Metrics and errors"]


def test_partial_errors_nonfinite_and_legacy_rejected(tmp_path):
    trace = write_run(
        tmp_path / "run",
        [{"task": {"data": {}}, "traces": [], "errors": [{"message": "failed"}]}],
    )
    with trace.open("a") as stream:
        stream.write('{"unfinished":\n')
        stream.write('{"task":{},"traces":[],"score":NaN}\n')
        stream.write('{"prompt":"legacy"}\n')
        stream.write(json.dumps({"task": {}, "traces": [{"version": 2}]}) + "\n")
    run = viewer.load_run(trace)
    assert len(run.episodes) == 1
    assert len(run.warnings) == 4
    assert (
        "No conversation recorded"
        in viewer.episode_sections(run.episodes[0])["Conversation"]
    )
    assert "failed" in viewer.episode_sections(run.episodes[0])["Metrics and errors"]


def test_scan_depth_and_resolved_config(tmp_path):
    write_run(tmp_path / "a" / "b", [episode()])
    write_run(tmp_path / "a" / "b" / "c", [episode()])
    config = tmp_path / "a" / "b" / "configs" / "resolved"
    config.mkdir(parents=True)
    (config / "eval.json").write_text('{"model":"test"}')
    runs = viewer.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].config == {"model": "test"}


@pytest.mark.asyncio
async def test_navigation(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import DataTable, Static

    first = viewer.load_run(
        write_run(tmp_path / "run-20261013-220000Z", [episode("one"), episode("two")])
    )
    second = viewer.load_run(
        write_run(tmp_path / "run-20261013-210000Z", [{"task": {}, "traces": []}])
    )
    second.model = "test-model"
    app = viewer.make_app([first, second])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("t", "down")
        await pilot.pause()
        assert app.query_one("#tasks", DataTable).cursor_row == 1
        await pilot.press("r", "down")
        await pilot.pause()
        assert app.selected_run == 1
        assert "No conversation recorded" in str(
            app.query_one("#content-1", Static).content
        )
        await pilot.press("q")


def test_loading_limits_are_explicit(tmp_path, monkeypatch):
    path = write_run(tmp_path / "run", [episode(), episode("second")])
    monkeypatch.setattr(viewer, "MAX_EPISODES", 1)
    run = viewer.load_run(path)
    assert len(run.episodes) == 1
    assert "safety limit" in run.warnings[0]
    monkeypatch.setattr(viewer, "MAX_FILE_BYTES", 1)
    run = viewer.load_run(path)
    assert not run.episodes
    assert "byte safety limit" in run.warnings[0]


def test_discovers_all_slurm_models_without_scanning_campaign_runtime(tmp_path):
    output = tmp_path / "outputs" / "hipergator"
    expected = set()
    for preset in ("gpt-oss-20b", "glimmer", "k2-horizon-7b", "lfm2-5-24b-a2b"):
        campaign = output / f"four-model-dev-{preset}"
        campaign.mkdir(parents=True)
        (campaign / "plan.json").write_text(json.dumps({"preset": preset}))
        expected.add(write_run(campaign / "evals" / preset, [episode(preset)]))
        write_run(campaign / "client-venv", [episode("not-an-evaluation")])
        write_run(campaign / "active-model", [episode("not-an-evaluation")])

    runs = viewer.discover_runs(output)
    assert {run.path for run in runs} == expected
    assert all(len(run.episodes) == 1 and not run.warnings for run in runs)
    assert {run.path for run in viewer.discover_runs(output.parent)} == expected
    one = next(iter(expected))
    assert [run.path for run in viewer.discover_runs(one.parents[2])] == [one]


def test_malformed_nested_evidence_is_reported(tmp_path):
    bad = episode()
    bad["traces"][0]["nodes"][0]["message"] = None
    run = viewer.load_run(write_run(tmp_path / "run", [bad, episode()]))
    assert len(run.episodes) == 1
    assert "message object" in run.warnings[0]


def test_prompt_messages_keep_gold_case_out_of_question():
    record = episode()
    record["task"]["data"] = {
        "prompt": [
            {"role": "user", "content": [{"type": "text", "text": "Find patient"}]}
        ],
        "case": {"gold_answer": "secret answer"},
        "info": {"private": "grading metadata"},
    }
    sections = viewer.episode_sections(record)
    assert sections["Question"] == "[user]\nFind patient"
    assert "secret answer" not in sections["Question"]
    assert "secret answer" in sections["Task data"]


def test_native_message_node_sampled_provenance():
    graph = pytest.importorskip("verifiers.v1.graph")
    types = pytest.importorskip("verifiers.v1.types")
    record = episode()
    record["traces"][0]["nodes"] = [
        graph.MessageNode(
            message=types.AssistantMessage(content="prompt context"), sampled=False
        ).model_dump(mode="json"),
        graph.MessageNode(
            parent=0,
            message=types.AssistantMessage(content="recorded reply"),
            sampled=True,
        ).model_dump(mode="json"),
    ]
    sections = viewer.episode_sections(record)
    assert "recorded reply" in sections["Final answer"]
    assert "prompt context" not in sections["Final answer"]
    assert "prompt context" in sections["Conversation"]


def test_correctness_never_uses_execution_ok_or_partial_reward():
    record = episode()
    record["traces"][0]["metrics"] = {"reward": 0.2}
    assert viewer.outcome(record) == "Unscored"
    record["traces"][0]["metrics"]["workflow_success"] = 0
    record["traces"][0]["metrics"]["strict_success"] = 1
    assert viewer.outcome(record) == "Incorrect"
    record["traces"][0]["metrics"]["workflow_success"] = 1
    record["traces"][0]["metrics"]["strict_success"] = 0
    assert viewer.outcome(record) == "Correct"
    record["errors"] = [{"message": "transport failure"}]
    assert viewer.outcome(record) == "Error"
    record["errors"] = []
    record["traces"][0]["ok"] = False
    assert viewer.outcome(record) == "Error"


def test_campaign_counts_distinguish_missing_errors_and_unscored(tmp_path):
    correct = episode("correct")
    incorrect = episode("incorrect")
    incorrect["traces"][0]["metrics"]["strict_success"] = 0
    failed = episode("failed")
    failed["ok"] = False
    unscored = episode("unscored")
    unscored["traces"][0]["metrics"] = {}
    run = viewer.Run(
        tmp_path / "traces.jsonl",
        episodes=[correct, incorrect, failed, unscored],
        requested=8,
    )
    assert viewer.score_label(run) == "1 / 8"
    assert viewer.run_counts(run) == {
        "Correct": 1,
        "Incorrect": 1,
        "Error": 1,
        "Unscored": 1,
    }
    run.episodes = []
    assert viewer.score_label(run) == "— / 8"


def test_unknown_token_usage_is_not_zero():
    record = episode()
    record["traces"][0]["calls"][0]["usage"] = None
    assert viewer.usage(record) == (1, None)
    record["traces"][0]["calls"][0]["usage"] = {
        "completion_tokens": 42,
        "reasoning_tokens": 17,
    }
    assert viewer.usage(record) == (1, 42)  # reasoning tokens are already included
    record["traces"][0]["calls"].append({"usage": None})
    assert viewer.usage(record) == (2, None)


def rendered(renderable):
    from io import StringIO

    from rich.console import Console

    stream = StringIO()
    Console(file=stream, width=90, color_system=None).print(renderable)
    return stream.getvalue()


def test_readable_rollout_pairs_calls_separates_reasoning_and_hides_prompt_examples():
    record = episode()
    nodes = record["traces"][0]["nodes"]
    nodes.insert(
        0,
        {
            "message": {"role": "assistant", "content": "DEMONSTRATION"},
            "sampled": False,
        },
    )
    nodes[2]["message"]["reasoning_content"] = "THINKING"
    nodes[2]["message"]["provider_state"] = [{"encrypted": "OPAQUE"}]
    nodes[2]["message"]["tool_calls"][0]["id"] = "call-a"
    nodes[3]["message"]["tool_call_id"] = "call-a"
    nodes[3]["message"]["content"] = "[not-rich-markup] plain tool text"
    nodes.insert(1, {"message": {"role": "tool", "content": "EXAMPLE RESULT"}})
    text = rendered(viewer.rollout_view(record))
    assert "Turn 1 · Assistant" in text
    assert "Call search" in text and "Result · search" in text
    assert "[not-rich-markup]" in text
    assert "EXAMPLE RESULT" not in text
    assert (
        "DEMONSTRATION" not in text and "THINKING" not in text and "OPAQUE" not in text
    )
    assert "THINKING" in rendered(viewer.rollout_view(record, reasoning=True))


def test_render_limits_expand_without_modifying_saved_content():
    original = "\n".join("line-" + str(i) for i in range(500))
    assert "Display shortened" in viewer.bounded(original)
    assert "line-350" not in viewer.bounded(original)
    assert "line-350" in viewer.bounded(original, expanded=True)
    assert "line-499" in original


@pytest.mark.asyncio
async def test_model_groups_newest_first_filters_and_refresh_preserve_task(tmp_path):
    from textual.widgets import DataTable, Input, Select

    older = viewer.Run(
        tmp_path / "run-20261013-210000Z/traces.jsonl",
        model="model-A",
        episodes=[episode("old")],
    )
    bad = episode("bad")
    bad["traces"][0]["metrics"]["strict_success"] = 0
    newer = viewer.Run(
        tmp_path / "run-20261013-220000Z/traces.jsonl",
        model="model-A",
        episodes=[episode("good"), bad],
    )
    other = viewer.Run(
        tmp_path / "run-20261013-200000Z/traces.jsonl",
        model="model-B",
        episodes=[episode("other")],
    )
    updated = [older, newer, other]
    app = viewer.make_app([older, other, newer], lambda: list(updated))
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert app.selected_model == "model-A"
        assert app.query_one("#models", DataTable).row_count == 2
        assert app.query_one("#runs", DataTable).row_count == 2
        assert app.selected_run == 0
        assert app.visible_tasks == [0, 1]
        app.query_one("#filter", Select).value = "Incorrect"
        await pilot.pause()
        assert app.visible_tasks == [1]
        assert app.selected_episode == 1
        app.query_one("#search", Input).value = "missing"
        await pilot.pause()
        assert app.visible_tasks == []
        app.query_one("#search", Input).value = ""
        app.query_one("#filter", Select).value = "All"
        await pilot.pause()
        assert app.selected_episode == 1
        updated.insert(
            0,
            viewer.Run(
                tmp_path / "run-20261013-230000Z/traces.jsonl",
                model="model-A",
                episodes=[episode("latest")],
            ),
        )
        await pilot.press("f")
        await pilot.pause()
        assert app.selected_run == 1  # same run, despite a newer run inserted above it
        assert app.selected_episode == 1
        await pilot.press("m", "down")
        await pilot.pause()
        assert app.selected_model == "model-B"
        assert app.query_one("#runs", DataTable).row_count == 1


@pytest.mark.asyncio
async def test_narrow_and_empty_viewers_can_refresh(tmp_path):
    from textual.widgets import DataTable, Select

    updated = []
    app = viewer.make_app([], lambda: list(updated))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.has_class("compact") and app.has_class("short")
        assert not app.query_one("#model-pane").display
        assert app.query_one("#compact-model").display
        await pilot.press("n", "p", "a", "e", "f")
        await pilot.pause()
        updated.append(
            viewer.Run(
                tmp_path / "traces.jsonl", model="new-model", episodes=[episode()]
            )
        )
        await pilot.press("f")
        await pilot.pause()
        assert app.query_one("#tasks", DataTable).row_count == 1
        assert app.query_one("#compact-model", Select).value == "new-model"


def test_repeated_attempts_group_interleaved_episodes(tmp_path):
    a1, b1, a2 = episode("a"), episode("b"), episode("a")
    a1["id"], b1["id"], a2["id"] = "a-first", "b-first", "a-second"
    run = viewer.Run(tmp_path / "traces.jsonl", episodes=[a1, b1, a2])
    rows = viewer.grouped_attempts(run)
    assert [row[:4] for row in rows] == [(0, 1, 1, 2), (2, 1, 2, 2), (1, 2, 1, 1)]
    assert "2 examples observed" in viewer.example_summary(run)
    assert viewer.episode_identity(a1) != viewer.episode_identity(a2)


def test_campaign_repeated_rollout_denominator(tmp_path):
    (tmp_path / "plan.json").write_text(
        json.dumps(
            {
                "num_examples": 32,
                "rollouts_per_example": 10,
            }
        )
    )
    run = viewer.load_campaign(tmp_path)
    assert run.requested == 320


@pytest.mark.asyncio
async def test_repeated_attempt_selection_keeps_distinct_rollouts(tmp_path):
    from textual.widgets import DataTable

    records = [episode("a"), episode("b"), episode("a")]
    for i, record in enumerate(records):
        record["id"] = f"attempt-{i}"
    app = viewer.make_app([viewer.Run(tmp_path / "traces.jsonl", episodes=records)])
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        assert app.visible_tasks == [0, 2, 1]
        app.query_one("#tasks", DataTable).move_cursor(row=1)
        await pilot.pause()
        assert app.selected_episode == 2
