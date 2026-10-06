"""Bundle only training/development data for eventual Prime Hub execution."""

import json
import shutil
import argparse
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--source", default="data/synthetic-hospital-v0.3.0")
args = parser.parse_args()
source = repo / args.source
target = repo / "environments/fhir_workflows/fhir_workflows/training_data"
target.mkdir(parents=True, exist_ok=True)
for split in ("train", "dev"):
    shutil.copytree(source / split, target / split, dirs_exist_ok=True)
manifest = json.loads((source / "manifest.json").read_text())
manifest["packaged_splits"] = ["train", "dev"]
manifest["files"] = {
    k: v for k, v in manifest["files"].items() if k.startswith(("train/", "dev/"))
}
(target / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n"
)
print(
    json.dumps(
        {
            "packaged_splits": manifest["packaged_splits"],
            "bytes": sum(p.stat().st_size for p in target.rglob("*") if p.is_file()),
        }
    )
)
