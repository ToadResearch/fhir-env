"""Launch one approved free revision pilot and retain its immutable receipt.

A saved attempt without a receipt blocks retries: inspect Prime first, because
an interrupted HTTP response does not establish that run creation failed.
"""

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "label",
        choices=[
            "laguna-raw",
            "laguna-assisted",
            "llama1b-native-retrieval",
            "llama1b-json-retrieval",
        ],
    )
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root / "artifacts/training"
    config = root / f"configs/revision-pilots/{options.label}.toml"
    content = config.read_bytes()
    recipe = tomllib.loads(content.decode())
    prices = json.loads((folder / "revision-model-prices.json").read_text())
    price = next(p for p in prices if p["name"] == recipe["model"])
    fields = [
        "effective_training_price_per_mtok",
        "effective_inference_input_price_per_mtok",
        "effective_inference_output_price_per_mtok",
    ]
    if any(price.get(field) != 0 for field in fields):
        raise SystemExit("This launcher only permits models recorded as free.")
    if any(e["id"] != "max/fhir-workflows@0.2.4" for e in recipe["env"]):
        raise SystemExit("Expected the verified immutable environment revision.")
    manifest = folder / "revision-pilot-runs.json"
    saved = (
        json.loads(manifest.read_text())
        if manifest.exists()
        else {
            "environment": "max/fhir-workflows@0.2.4",
            "free_models_only": True,
            "runs": [],
        }
    )
    if any(r["name"] == recipe["name"] for r in saved["runs"]):
        raise SystemExit("Run already recorded; no duplicate launch performed.")
    attempt = folder / f"{options.label}-v0.2.4-attempt.json"
    if attempt.exists():
        raise SystemExit("Prior attempt found. Inspect Prime before any retry.")
    frozen = folder / f"revision-configs/{config.name}"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_bytes(content)
    attempt.write_text(
        json.dumps(
            {
                "attempted_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "config": str(frozen.relative_to(root)),
                "config_sha256": hashlib.sha256(content).hexdigest(),
                "status": "CREATION_REQUEST_STARTING",
            },
            indent=2,
        )
        + "\n"
    )
    result = subprocess.run(
        [
            "prime",
            "--plain",
            "train",
            str(frozen),
            "--yes",
            "--output",
            "json",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    log = folder / f"{options.label}-v0.2.4-launch.log"
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(f"Launch returned {result.returncode}; inspect {log}.")
    decoder = json.JSONDecoder()
    receipt = None
    for position, character in enumerate(result.stdout):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(result.stdout[position:])
        except ValueError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("run"), dict):
            receipt = candidate
            break
    if receipt is None:
        raise SystemExit(f"No run receipt; inspect {log} before retrying.")
    (folder / f"{options.label}-v0.2.4-launch.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    run = receipt["run"]
    row = {
        "id": run["id"],
        "name": run["name"],
        "model": run["base_model"],
        "profile": recipe["env"][0]["args"]["tool_profile"],
        "protocol": recipe["env"][0]["args"]["tool_protocol"],
        "config": str(frozen.relative_to(root)),
        "config_sha256": hashlib.sha256(content).hexdigest(),
        "max_steps": run["max_steps"],
        "status_at_creation": run["status"],
        "created_at": run["created_at"],
        "notice": run.get("notice"),
        "environment_version": run["environments"][0]["version"],
        "url": f"https://app.primeintellect.ai/dashboard/training/{run['id']}/metrics",
    }
    saved["runs"].append(row)
    manifest.write_text(json.dumps(saved, indent=2) + "\n")
    if row["environment_version"] != "0.2.4":
        raise SystemExit("Unexpected version in receipt. Inspect the created run.")
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
