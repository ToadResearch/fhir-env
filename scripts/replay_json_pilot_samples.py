"""Independently replay saved JSON-action samples against the local chart store.

This validates saved training actions, not an unbiased development accuracy.
Only the bounded environment tool registry executes these explicit actions.
"""

import argparse
import asyncio
import json
import tomllib
from pathlib import Path

import verifiers as vf

from fhir_workflows.fhir_workflows import final_text, load_environment
from fhir_workflows.protocol import is_final
from fhir_workflows.scoring import evaluate_training


def content_from_bridge(text):
    # Prime's sample display escapes message content inside a serialized string.
    # Decode that representation only; never repair action JSON or evaluate code.
    try:
        return json.loads('"' + text + '"')
    except (ValueError, TypeError):
        return text


async def replay(env, sample, arguments):
    row = env.dataset[sample["problem_id"]]
    messages = json.loads(sample["completion"])
    messages = [
        {**m, "content": content_from_bridge(m.get("content", ""))} for m in messages
    ]
    prefix = len(row["prompt"])
    assert messages[prefix - 1]["content"] == row["prompt"][-1]["content"]
    state = vf.State(
        {"info": row["info"], "prompt": row["prompt"], "answer": "", "task": "default"}
    )
    await env.setup_state(state)
    for index, message in enumerate(messages[prefix:], prefix):
        if message["role"] != "assistant":
            continue
        if not is_final(message["content"], allow_fence=True):
            await env.env_response(messages[: index + 1], state)
    scored = evaluate_training(
        state["fhir_task"],
        state["fhir_store"],
        final_text(messages),
        answer_mode=arguments["answer_mode"],
        retrieval_weight=arguments["retrieval_weight"],
    )
    assert abs(scored["reward"] - sample["reward"]) < 1e-6
    return {
        "problem_id": sample["problem_id"],
        "sample_id": sample["sample_id"],
        "task_id": row["info"]["task_id"],
        "reward": scored["reward"],
        "strict_success": scored["strict_success"],
        "workflow_success": scored["workflow_success"],
        "retrieval_shaping": scored["retrieval_shaping"],
        "tool_calls": scored["tool_calls"],
        "first_evidence_round": scored["first_evidence_round"],
        "events": state["fhir_store"].events,
        "final": final_text(messages),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/training/json-pilot-replay.json")
    )
    options = parser.parse_args()
    recipe = tomllib.loads(options.config.read_text())
    arguments = recipe["env"][0]["args"]
    assert arguments["tool_protocol"] == "json_action"
    env = load_environment(**arguments)
    samples = json.loads(options.samples.read_text())["samples"]
    results = [asyncio.run(replay(env, s, arguments)) for s in samples]
    report = {
        "samples_source": str(options.samples),
        "config": str(options.config),
        "replayed_samples": len(results),
        "reward_matches": len(results),
        "strict_successes": sum(r["strict_success"] for r in results),
        "partial_credit_only": sum(0 < r["reward"] < 1 for r in results),
        "zero_reward": sum(r["reward"] == 0 for r in results),
        "limits": "Saved training samples may be post-filter biased; this is action/verifier replay, not dev accuracy. Evidence rounds exclude demonstrations in corrected v0.2.5 replay.",
        "results": results,
    }
    options.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
