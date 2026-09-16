"""Start the MCP server the way ZCode will and confirm it answers.

Used by the installers as a pre-flight check: the configuration is not touched
unless the server actually starts and advertises its tools.

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "math_rigor_server.py"
MINIMUM_TOOLS = 20


async def run() -> int:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("  ERROR the `mcp` package is not installed in this interpreter:")
        print(f"        {sys.executable}")
        print("        run:  pip install -r requirements.txt")
        return 1

    if not SERVER.exists():
        print(f"  ERROR server script missing: {SERVER}")
        return 1

    env = {**os.environ, "MATH_RIGOR_HOME": str(ROOT)}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"  handshake     : {init.server_info.name} v{init.server_info.version}")
            tools = (await session.list_tools()).tools
            print(f"  tools         : {len(tools)}")
            if len(tools) < MINIMUM_TOOLS:
                print(f"  ERROR expected at least {MINIMUM_TOOLS} tools, got {len(tools)}")
                return 1

            result = await session.call_tool("verify_forall",
                                             {"statement": "forall n in Z: 6 | n^3 - n"})
            payload = json.loads(result.content[0].text)
            status = payload.get("status")
            print(f"  proof engine  : {status} ({payload.get('method')})")
            if status != "proven":
                print("  ERROR the proof engine did not prove a known-true statement")
                return 1

            if not (init.instructions and "proof_start" in init.instructions):
                print("  WARNING server instructions were not delivered")
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR the server did not start: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
