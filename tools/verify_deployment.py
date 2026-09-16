"""Verify the deployed configuration the way ZCode will actually use it.

Reads `mcp.servers.math-rigor` out of the real user config, launches that exact
command/args/env from an unrelated working directory, and drives the server over
stdio.  This is the check that matters: it proves the deployment, not the source
tree, works.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path.home() / ".zcode" / "cli" / "config.json"
WORKDIR = os.path.abspath(os.sep)  # deliberately unrelated to the server directory


def load_entry() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return config["mcp"]["servers"]["math-rigor"]


async def main() -> int:
    entry = load_entry()
    print(f"config: {CONFIG}")
    print(f"command: {entry['command']}")
    print(f"args   : {entry['args']}")
    print(f"env    : {entry['env']}")
    print(f"cwd    : {WORKDIR} (unrelated on purpose)")
    print()

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, **entry.get("env", {})}
    params = StdioServerParameters(command=entry["command"], args=entry["args"],
                                   env=env, cwd=WORKDIR)

    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"OK   handshake with {init.server_info.name} v{init.server_info.version}")
            tools = (await session.list_tools()).tools
            print(f"OK   {len(tools)} tools advertised")
            if len(tools) != 23:
                print(f"FAIL expected 23 tools, got {len(tools)}")
                failures += 1

            async def call(name, args):
                result = await session.call_tool(name, args)
                text = result.content[0].text if result.content else ""
                try:
                    return json.loads(text)
                except Exception:
                    return {"_raw": text}

            wf = await call("proof_workflow", {})
            if isinstance(wf, dict) and wf.get("stages"):
                print("OK   proof_workflow responds with the stage list")
            else:
                print(f"FAIL proof_workflow: {str(wf)[:160]}")
                failures += 1

            # The instruction preamble must reach the client: it is what tells the
            # model how to use the server before it calls anything.
            if init.instructions and "proof_start" in init.instructions:
                print("OK   server instructions reached the client")
            else:
                print("FAIL server instructions were not delivered")
                failures += 1

            proven = await call("verify_forall", {"statement": "forall n in Z: 6 | n^3 - n"})
            if proven.get("status") == "proven":
                print(f"OK   verify_forall proved 6 | n^3-n via: {proven.get('method')}")
            else:
                print(f"FAIL verify_forall: {str(proven)[:200]}")
                failures += 1

            refuted = await call("find_counterexample", {"claim": "forall n in R: n^2 >= n"})
            if refuted.get("verdict") == "refuted" and refuted.get("counterexample"):
                print(f"OK   counterexample found: {refuted['counterexample']}")
            else:
                print(f"FAIL find_counterexample: {str(refuted)[:200]}")
                failures += 1

            # proof sessions must land in the directory named by MATH_RIGOR_HOME
            start = await call("proof_start", {
                "problem": "deployment smoke test",
                "goal": "forall n in Z: n^2 >= 0",
                "variables": {"n": "int"},
            })
            sid = start.get("session_id")
            stored = start.get("stored_at", "")
            home = entry.get("env", {}).get("MATH_RIGOR_HOME", "")
            if sid and home and str(stored).startswith(home):
                print(f"OK   proof sessions are stored under MATH_RIGOR_HOME ({stored})")
            else:
                print(f"FAIL session storage path unexpected: {stored!r} vs home {home!r}")
                failures += 1
            await call("proof_add_step", {"session_id": sid, "statement": "n^2 >= 0",
                                          "rule": "verify_inequality",
                                          "justification": "smoke test"})
            audit = await call("proof_validate", {"session_id": sid})
            if audit.get("verdict") in ("verified", "sound_with_gaps", "flawed"):
                print(f"OK   audit ran end to end: verdict={audit.get('verdict')}")
            else:
                print(f"FAIL audit: {str(audit)[:200]}")
                failures += 1

    print()
    print("deployment verified" if not failures else f"{failures} deployment check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
