"""Native local harness: run Verifiers' tool loop with the locked interpreter."""

import json
import sys
from pathlib import Path

from verifiers.v1.configs.harness import HarnessConfig
from verifiers.v1.dialects.chat import message_to_wire
from verifiers.v1.harness import Harness
from verifiers.v1.harnesses import null
from verifiers.v1.runtimes.subprocess import SubprocessRuntime

__all__ = ["FhirLockedHarness"]


class FhirLockedHarnessConfig(HarnessConfig):
    id: str = "fhir-locked-harness"


class FhirLockedHarness(Harness[FhirLockedHarnessConfig]):
    APPENDS_SYSTEM_PROMPT = True
    SUPPORTS_MCP = True
    SUPPORTS_RESUME = True
    EXECUTES_CODE = False
    NEEDS_CONTAINER = False

    async def setup(self, runtime):
        if not isinstance(runtime, SubprocessRuntime):
            raise TypeError(
                "The locked FHIR harness requires the local subprocess runtime"
            )
        if not Path(null.__file__).with_name("program.py").is_file():
            raise RuntimeError("The locked Verifiers tool-loop program is missing")

    async def launch(self, ctx, trace, runtime, endpoint, secret, mcp_urls, data):
        system_prompt, prompt = self.resolve_prompt(data)
        args = [
            f"--base-url={endpoint}",
            f"--api-key={secret}",
            f"--model={ctx.model}",
        ]
        if system_prompt:
            args.append(f"--system-prompt={system_prompt}")
        if mcp_urls:
            args.append(
                "--mcp-config="
                + json.dumps(
                    {
                        "mcpServers": {
                            name: {"url": url, "timeout": self.config.tool_timeout}
                            for name, url in mcp_urls.items()
                        }
                    }
                )
            )
        if isinstance(prompt, str):
            args.append(f"--prompt={prompt}")
        elif prompt is not None:
            path = f".vf-initial-messages-{trace.id}.json"
            await runtime.write(
                path, json.dumps([message_to_wire(m) for m in prompt]).encode()
            )
            args.append(f"--initial-messages-file={path}")
        # CPU uv sync installs these dependencies. The upstream program is used
        # unchanged; its PEP 723 installation path is deliberately not invoked.
        return await runtime.run_program(
            [sys.executable, str(Path(null.__file__).with_name("program.py")), *args],
            self.config.resolved_env,
        )
