"""Native v1 trace parsing and offline terminal navigation."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "view_traces", Path(__file__).parents[1] / "scripts" / "view_traces.py"
)
viewer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = viewer
spec.loader.exec_module(viewer)


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
        write_run(tmp_path / "first", [episode("one"), episode("two")])
    )
    second = viewer.load_run(
        write_run(tmp_path / "second", [{"task": {}, "traces": []}])
    )
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
            app.query_one("#content-2", Static).content
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
