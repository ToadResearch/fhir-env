"""Prepare local, separated HF-ready files; never contacts HF or uploads."""

import hashlib
import json
import shutil
import argparse
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--source", default="data/synthetic-hospital-v0.2.1")
args = parser.parse_args()
source = repo / args.source
target = repo / "artifacts/hf-release"
target.mkdir(parents=True, exist_ok=True)
for split in ("train", "dev", "public", "heldout"):
    scoring = target / "scoring" / f"{split}.jsonl"
    prompts = target / "tasks" / f"{split}.jsonl"
    scoring.parent.mkdir(parents=True, exist_ok=True)
    prompts.parent.mkdir(parents=True, exist_ok=True)
    with (source / split / "tasks.jsonl").open() as src, scoring.open(
        "w"
    ) as gold, prompts.open("w") as tasks:
        for line in src:
            task = json.loads(line)
            task["shard"] = "resources/" + split + "/" + Path(task["shard"]).name
            gold.write(json.dumps(task, sort_keys=True) + "\n")
            public = {
                k: task[k]
                for k in (
                    "id",
                    "patient_id",
                    "split",
                    "family",
                    "dependency_depth",
                    "snapshot_date",
                    "prompt",
                    "shard",
                )
            }
            tasks.write(json.dumps(public, sort_keys=True) + "\n")
    shutil.copytree(
        source / split / "shards", target / "resources" / split, dirs_exist_ok=True
    )
shutil.copy2(source / "manifest.json", target / "source-manifest.json")
shutil.copy2(source / "provenance-ledger.jsonl", target / "provenance-ledger.jsonl")
shutil.copy2(repo / "docs/dataset-card.md", target / "README.md")
shutil.copy2(repo / "sources/hospital/LICENSE", target / "SYNTHETIC_HOSPITAL_LICENSE")
shutil.copy2(repo / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
release_manifest = {
    "source_version": json.loads((source / "manifest.json").read_text())["version"],
    "files": {
        str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(target.rglob("*"))
        if p.is_file() and p.name != "release-manifest.json"
    },
}
(target / "release-manifest.json").write_text(
    json.dumps(release_manifest, indent=2, sort_keys=True) + "\n"
)
print(
    json.dumps(
        {
            "prepared_at": str(target),
            "uploaded": False,
            "files": sum(p.is_file() for p in target.rglob("*")),
        }
    )
)
