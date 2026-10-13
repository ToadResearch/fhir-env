"""Exercise native v1 episodes against a mocked provider and real MCP tools."""

import asyncio
import json
import os
from pathlib import Path

import verifiers.v1 as vf
from aiohttp import web
from fhir_query_rl import load_environment
from verifiers.v1.runtimes.base import Runtime


def test_native_mocked_episode_retrieval_writes_stopping_and_isolation(
    monkeypatch, tmp_path
):
    async def prepare(self, script, env=None, **kwargs):
        raise AssertionError("Evaluation must not install a uv script environment")

    monkeypatch.setattr(Runtime, "prepare_uv_script", prepare)
    monkeypatch.setenv("UV_OFFLINE", "1")

    async def run():
        env = load_environment(
            pilot=True, split="train", family="coverage_change", max_examples=1
        )
        task = next(iter(env.taskset))
        steps = task.data.case["reference_steps"]

        async def provider(request):
            payload = await request.json()
            count = sum(m["role"] == "assistant" for m in payload["messages"])
            schemas = payload.get("tools") or []
            assert schemas and all(
                "_store" not in t["function"]["parameters"].get("properties", {})
                for t in schemas
            )
            if count < len(steps):
                step = steps[count]
                arguments = {"method": step["method"], "path": step["path"]}
                for source, target in [
                    ("body", "body_json"),
                    ("headers", "headers_json"),
                ]:
                    if source in step:
                        arguments[target] = json.dumps(step[source])
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{count}",
                            "type": "function",
                            "function": {
                                "name": "fhir_request",
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                message = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "answer": task.data.case["gold"]["answer"],
                            "evidence": task.data.case["gold"]["evidence"],
                        }
                    ),
                }
                finish = "stop"
            return web.json_response(
                {
                    "id": "mock",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "mock",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                }
            )

        app = web.Application()
        app.router.add_post("/v1/chat/completions", provider)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            monkeypatch.setenv("LOCAL_TEST_KEY", "test")
            ctx = vf.ModelContext(
                "mock",
                vf.EvalClientConfig(
                    base_url=f"http://127.0.0.1:{port}/v1", api_key_var="LOCAL_TEST_KEY"
                ),
            )
            async with env.serving():
                first = await env.run_episode(task, ctx)
                second = await env.run_episode(task, ctx)
            for episode in [first, second]:
                assert episode.ok, "\n".join(
                    e.traceback or e.message
                    for e in episode.errors
                    + [e for t in episode.traces for e in t.errors]
                )
                trace = episode.traces[0]
                assert trace.metrics["strict_success"] == 1
                assert len(trace.calls) == len(steps) + 1
                assert trace.metrics["model_input_tokens"] == 100 * (len(steps) + 1)
                assert trace.metrics["model_output_tokens"] == 20 * (len(steps) + 1)
                assert any(
                    e["method"] in ["POST", "PUT"] for e in trace.info["fhir_trace"]
                )
                assert "state" not in trace.model_dump(mode="json")
                assert trace.info["fhir_trace"]
                assert [event["round"] for event in trace.info["fhir_trace"]] == list(
                    range(1, len(steps) + 1)
                )
            assert (
                first.traces[0].info["fhir_trace"]
                == second.traces[0].info["fhir_trace"]
            )
            if sample_dir := os.environ.get("FHIR_NATIVE_EVAL_SAMPLE"):
                destination = Path(sample_dir)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "traces.jsonl").write_text(
                    first.model_dump_json() + "\n", encoding="utf-8"
                )
        finally:
            await runner.cleanup()

    asyncio.run(run())
