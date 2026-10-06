"""Guard the deployed Prime legacy-to-v1 trace protocol, not just local scoring."""

from verifiers.v1.legacy import rollout_output_to_trace
from verifiers.v1.serve.types import RunRolloutResponse


def test_hosted_trace_retains_training_tokens_usage_and_truncation():
    prompt = [{"role": "user", "content": "Read the synthetic chart."}]
    out = {
        "prompt": prompt,
        "model": "poolside/Laguna-XS-2.1",
        "reward": 0.0,
        "metrics": {"strict_success": 0.0, "tool_calls": 1.0},
        "is_completed": True,
        "trajectory": [
            {
                "prompt": prompt,
                "response": {
                    "message": {"content": "partial", "finish_reason": "length"},
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
                "tokens": {
                    "prompt_ids": [1, 2, 3],
                    "completion_ids": [4, 5],
                    "completion_logprobs": [-0.2, -0.3],
                },
            }
        ],
    }
    wire = rollout_output_to_trace(out, 0).model_dump()
    # These fields moved off MessageNode in the deployed runtime. The old
    # 0.2.0 wheel emitted them there and the trainer rejected every rollout.
    assert all("usage" not in n and "finish_reason" not in n for n in wire["nodes"])
    received = RunRolloutResponse.model_validate({"trace": wire}).trace
    assert received.is_truncated
    assert received.calls[0].usage.completion_tokens == 2
    branch = received.branches[0]
    assert branch.token_ids == [1, 2, 3, 4, 5]
    assert branch.sampled_mask == [False, False, False, True, True]
    assert branch.logprobs[-2:] == [-0.2, -0.3]
    assert received.metrics["strict_success"] == 0.0
