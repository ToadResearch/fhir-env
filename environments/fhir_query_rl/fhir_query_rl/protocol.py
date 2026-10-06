"""Explicit JSON action transport; never infer or repair executable commands."""

import json
import re


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-JSON numeric constant: {value}")


def parse_object(text, allow_fence=False):
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError("Expected one JSON object, at most 65536 characters")
    if allow_fence:
        match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", text, re.DOTALL)
        if match:
            text = match.group(1)
    result = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
    if not isinstance(result, dict):
        raise ValueError("Expected one JSON object")
    return result


def is_final(text, allow_fence=False):
    try:
        return set(parse_object(text, allow_fence)) == {"answer", "evidence"}
    except (ValueError, TypeError):
        return False


ACTION_INSTRUCTIONS = """
Use the JSON action interface. Native function calling is disabled in this profile.
Each assistant turn must be exactly ONE JSON object, without explanation or code
fences. To invoke a tool, use {"tool":"TOOL_NAME","args":{...}}. Supply argument
values, not function definitions or JSON schemas. The next user message contains
the tool observation. Only explicitly invoked, validated actions are executed.
For a final answer use {"answer":{...},"evidence":["Type/id",...]}, as requested.
Only cite the resources needed to support the requested fields or change.
Example action (replace example values with the caller's actual information):
{"tool":"fhir_request","args":{"method":"GET","path":"Patient?identifier=https://fhir-workflows.example/mrn|EXAMPLE-MRN&_count=5","body_json":"","headers_json":""}}
"""
