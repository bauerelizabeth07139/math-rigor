"""Install the math-rigor suite into ZCode's user scope.

Works both from a cloned repository and from an existing installation, because
every path is derived from this file's location rather than hard-coded:

    <root>/tools/install.py   ->   <root>/server/math_rigor_server.py
                                   <root>/venv/Scripts/python.exe  (Windows)
                                   <root>/venv/bin/python          (POSIX)

What it does:

1. registers `mcp.servers.math-rigor` in `~/.zcode/cli/config.json`;
2. copies `skills/math-rigor/` into `~/.zcode/skills/`;
3. copies `commands/*.md` into `~/.zcode/commands/`.

Every step is idempotent.  An existing `config.json` is backed up first, and a
config that cannot be parsed is never overwritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "math_rigor_server.py"


def venv_python(root: Path) -> Path:
    candidates = [
        root / "venv" / "Scripts" / "python.exe",  # Windows
        root / "venv" / "bin" / "python",          # POSIX
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # fall back to the interpreter running this script
    return Path(sys.executable)


def zcode_home() -> Path:
    override = os.environ.get("ZCODE_HOME")
    return Path(override) if override else Path.home() / ".zcode"


def register_mcp(home: Path, python: Path) -> bool:
    config_path = home / "cli" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        raw = config_path.read_text(encoding="utf-8")
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  ERROR {config_path} is not valid JSON ({exc}); leaving it untouched")
            print("        fix it by hand, then re-run this installer")
            return False
        backup = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup)
        print(f"  backed up the previous config to {backup.name}")
    else:
        config = {}
        print("  no user config yet; creating one")

    mcp = config.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        print("  ERROR `mcp` exists but is not an object; leaving the config untouched")
        return False
    servers = mcp.setdefault("servers", {})
    if not isinstance(servers, dict):
        print("  ERROR `mcp.servers` exists but is not an object; leaving the config untouched")
        return False

    entry = {
        "command": str(python),
        "args": [str(SERVER)],
        "env": {"MATH_RIGOR_HOME": str(ROOT)},
        "enabled": True,
        # Residue-split proofs of divisibility statements can take a minute or
        # more, so the client must not cut them off early.
        "timeoutMs": 300000,
    }
    existed = servers.get("math-rigor") is not None
    servers["math-rigor"] = entry
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    readback = json.loads(config_path.read_text(encoding="utf-8"))
    if readback.get("mcp", {}).get("servers", {}).get("math-rigor") != entry:
        print("  ERROR the written entry does not read back identically")
        return False
    print(f"  {'updated' if existed else 'registered'} mcp.servers.math-rigor")
    print(f"    command : {entry['command']}")
    print(f"    server  : {entry['args'][0]}")
    print(f"    sessions: {entry['env']['MATH_RIGOR_HOME']}")
    return True


def copy_tree(source: Path, target: Path, label: str) -> bool:
    if not source.is_dir():
        print(f"  SKIP {label}: {source} not found")
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    count = sum(1 for path in target.rglob("*") if path.is_file())
    print(f"  installed {label} -> {target} ({count} files)")
    return True


def copy_commands(source: Path, target: Path) -> bool:
    if not source.is_dir():
        print(f"  SKIP commands: {source} not found")
        return False
    target.mkdir(parents=True, exist_ok=True)
    names = []
    for path in sorted(source.glob("*.md")):
        shutil.copy2(path, target / path.name)
        names.append("/" + path.stem)
    print(f"  installed commands -> {target} ({', '.join(names)})")
    return True


def main() -> int:
    home = zcode_home()
    python = venv_python(ROOT)

    print(f"repository root : {ROOT}")
    print(f"zcode home      : {home}")
    print(f"interpreter     : {python}")
    print()

    if not SERVER.exists():
        print(f"ERROR server script missing: {SERVER}")
        return 1
    if not python.exists():
        print(f"ERROR interpreter missing: {python}")
        print("      create the virtualenv first (see README, step 1)")
        return 1

    print("MCP server")
    ok_mcp = register_mcp(home, python)
    print()
    print("skill")
    ok_skill = copy_tree(ROOT / "skills" / "math-rigor",
                         home / "skills" / "math-rigor", "skill math-rigor")
    print()
    print("slash commands")
    ok_commands = copy_commands(ROOT / "commands", home / "commands")
    print()

    if ok_mcp and ok_skill and ok_commands:
        print("installed.  Start a new ZCode session so the server connects, then try:")
        print("  /prove 对每个整数 n，n^3 - n 能被 6 整除")
        return 0
    print("finished with problems; see the messages above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
