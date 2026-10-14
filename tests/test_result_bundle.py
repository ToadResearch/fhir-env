"""A review export must contain results and must never remove source runs."""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "bundle_results", ROOT / "scripts/bundle_hipergator_results.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def campaign(exporter, tmp_path):
    base = tmp_path / "runs"
    for preset in exporter.PRESETS:
        run = base / ("review-" + preset)
        write_json(run / "plan.json", {"preset": preset, "num_examples": 2})
        write_json(
            run / "receipts/finished.json", {"status": "completed", "episodes": 2}
        )
        trace = run / "evals" / preset / "traces.jsonl"
        trace.parent.mkdir(parents=True)
        # Include an error episode: a completed evaluation need not have all
        # successful model calls, and those failures are essential for analysis.
        trace.write_text(
            json.dumps({"task": {"id": "a"}, "traces": [{"version": 1}]})
            + "\n"
            + json.dumps({"task": {"id": "b"}, "traces": [], "error": "timeout"})
            + "\n"
        )
        (run / "logs").mkdir()
        (run / "logs/gpu.log").write_text("evaluation completed")
        (run / "logs/.env").write_text("private")
        (run / "logs/credentials.json").write_text("private")
        (run / "client-venv").mkdir()
        (run / "client-venv/private.json").write_text("private")
    return base, tmp_path / "review.zip"


def test_verified_bundle_retains_sources_and_ignores_newer_duplicates(
    exporter, campaign
):
    base, output = campaign
    for preset in exporter.PRESETS:
        write_json(base / ("newer-" + preset) / "plan.json", {"preset": preset})
    before = {p: p.read_bytes() for p in base.rglob("*") if p.is_file()}
    manifest = exporter.bundle(base, "review", output)
    assert set(manifest["runs"]) == set(exporter.PRESETS)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("manifest.json")) == manifest
        for path, digest in manifest["files_sha256"].items():
            assert hashlib.sha256(archive.read(path)).hexdigest() == digest
        for entry in manifest["runs"].values():
            assert entry["episodes"] == 2
            assert len(archive.read(entry["traces"][0]).splitlines()) == 2
        assert not any(
            word in name
            for name in archive.namelist()
            for word in ("private", "credentials", ".env", "client-venv", "newer-")
        )
    assert {p: p.read_bytes() for p in base.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "fault",
    ["no_receipt", "failed", "no_traces", "empty", "count", "malformed", "legacy"],
)
def test_incomplete_or_invalid_results_never_publish(exporter, campaign, fault):
    base, output = campaign
    run = base / "review-glimmer"
    receipt = run / "receipts/finished.json"
    trace = run / "evals/glimmer/traces.jsonl"
    if fault == "no_receipt":
        receipt.unlink()
    elif fault == "failed":
        write_json(receipt, {"status": "failed", "episodes": 2})
    elif fault == "no_traces":
        trace.unlink()
    elif fault == "empty":
        trace.write_text("")
    elif fault == "count":
        write_json(receipt, {"status": "completed", "episodes": 32})
    elif fault == "malformed":
        trace.write_text('{"truncated":')
    else:
        trace.write_text('{"prompt": "legacy", "reward": 1}\n')
    before = {p: p.read_bytes() for p in base.rglob("*") if p.is_file()}
    with pytest.raises((OSError, ValueError, TypeError)):
        exporter.bundle(base, "review", output)
    assert not output.exists()
    assert not list(output.parent.glob(".fhir-review-*"))
    assert {p: p.read_bytes() for p in base.rglob("*") if p.is_file()} == before


def test_existing_archive_is_not_replaced(exporter, campaign):
    base, output = campaign
    output.write_bytes(b"existing archive")
    with pytest.raises(FileExistsError):
        exporter.bundle(base, "review", output)
    assert output.read_bytes() == b"existing archive"


def test_symlinked_trace_rejected(exporter, campaign, tmp_path):
    base, output = campaign
    trace = base / "review-glimmer/evals/glimmer/traces.jsonl"
    target = tmp_path / "outside.jsonl"
    trace.rename(target)
    trace.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        exporter.bundle(base, "review", output)
    assert not output.exists()
    assert target.exists()


def test_changed_source_rejected_after_zip_creation(exporter, campaign, monkeypatch):
    base, output = campaign
    original = zipfile.ZipFile.write

    def change_before_copy(archive, filename, *args, **kwargs):
        if Path(filename).name == "gpu.log":
            Path(filename).write_text("changed while copying")
        return original(archive, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", change_before_copy)
    with pytest.raises(ValueError, match="Source changed"):
        exporter.bundle(base, "review", output)
    assert not output.exists()
    assert not list(output.parent.glob(".fhir-review-*"))
    assert len(list(base.glob("*/plan.json"))) == 4


def test_repeated_rollout_export(exporter, campaign):
    base, output = campaign
    for preset in exporter.PRESETS:
        write_json(
            base / ("review-" + preset) / "plan.json",
            {
                "preset": preset,
                "num_examples": 1,
                "rollouts_per_example": 2,
            },
        )
    manifest = exporter.bundle(base, "review", output)
    assert all(result["episodes"] == 2 for result in manifest["runs"].values())
