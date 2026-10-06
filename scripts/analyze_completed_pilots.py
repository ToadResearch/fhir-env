"""Extract unfiltered development measurements from a saved Prime snapshot."""

import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/training/completed-v0.2.3")
    )
    options = parser.parse_args()
    summary = json.loads((options.snapshot / "summary.json").read_text())
    rows = []
    for run in summary["runs"]:
        metrics = json.loads(
            (options.snapshot / run["id"] / "metrics.json").read_text()
        )["metrics"]
        for metric in sorted(metrics, key=lambda value: value["step"]):
            keys = [
                k
                for k, v in metric.items()
                if k.startswith("eval/") and k.endswith("/all/avg@1") and v is not None
            ]
            for key in keys:
                prefix = key.removesuffix("avg@1")
                rows.append(
                    {
                        "run_id": run["id"],
                        "name": run["name"],
                        "profile": run["profile"],
                        "orchestrator_step": metric["step"],
                        "measurement": "baseline"
                        if not any(r["run_id"] == run["id"] for r in rows)
                        else "evaluation",
                        "dev_cases": 12,
                        "dev_success_fraction": metric[key],
                        "dev_success_count": round(metric[key] * 12),
                        "operation_calls_mean": metric.get(
                            prefix + "metrics/tool_calls/mean"
                        ),
                        "agent_tool_calls_mean": metric.get(
                            prefix + "metrics/agent_tool_calls/mean"
                        ),
                        "truncated_fraction": metric.get(prefix + "is_truncated/mean"),
                    }
                )
    result = {
        "snapshot": str(options.snapshot),
        "captured_at_utc": summary["captured_at_utc"],
        "environment": "max/fhir-workflows@0.2.3",
        "rows": rows,
        "limits": [
            "Twelve development cases; no heldout measurement.",
            "Asynchronous orchestrator steps are not necessarily policy update counts.",
            "Operation calls averaged across successes and failures are not time-to-answer.",
            "The supplied-chart control has a different task distribution and access model.",
            "Saved post-filter training samples are not an unbiased accuracy denominator.",
        ],
        "total_recorded_cost_usd": sum(r["total_cost_usd"] for r in summary["runs"]),
        "interpretation": "Raw and assisted dev accuracy remained 2/12; no search efficiency improvement demonstrated.",
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    with options.output.with_suffix(".csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            {
                "evaluations": len(rows),
                "output": str(options.output),
                "interpretation": result["interpretation"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
