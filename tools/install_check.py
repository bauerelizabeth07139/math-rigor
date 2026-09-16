"""Validate the ZCode-side artifacts against ZCode's own loading rules.

Checks the skill, the slash commands, and the MCP registration the way the client
will read them, and reports any shadowing that would silently replace them.
"""

import json
import os
import re
import sys
from pathlib import Path

HOME = Path.home()
SKILL_DIRS = [HOME / ".zcode" / "skills", HOME / ".agents" / "skills"]
COMMAND_DIRS = [HOME / ".zcode" / "commands", HOME / ".agents" / "commands"]
CONFIG = HOME / ".zcode" / "cli" / "config.json"

COMMAND_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

problems: list[str] = []
notes: list[str] = []


def ok(message: str) -> None:
    print(f"OK   {message}")


def bad(message: str) -> None:
    problems.append(message)
    print(f"FAIL {message}")


def read_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """Parse the flat frontmatter the client uses (single-line keys only)."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    block = text[3:end]
    body = text[end + 4:]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.startswith((" ", "\t")):
            # indented lines belong to a multi-line value, which the flat parser drops
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


print("=" * 72)
print("skill")
print("=" * 72)
found_skills: list[Path] = []
for root in SKILL_DIRS:
    if not root.is_dir():
        notes.append(f"skill root does not exist (fine if unused): {root}")
        continue
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            found_skills.append(entry / "SKILL.md")

target_skill = HOME / ".zcode" / "skills" / "math-rigor" / "SKILL.md"
if not target_skill.exists():
    bad(f"skill not found at {target_skill}")
else:
    fields, body = read_frontmatter(target_skill)
    name = fields.get("name", "")
    description = fields.get("description", "")
    if name != "math-rigor":
        bad(f"frontmatter name is {name!r}, expected 'math-rigor'")
    elif not SKILL_NAME_RE.match(name):
        bad(f"skill name {name!r} violates {SKILL_NAME_RE.pattern}")
    else:
        ok(f"name '{name}' matches the directory and is valid")
    if not description:
        bad("description is missing or was dropped by the flat frontmatter parser")
    elif "\n" in description:
        bad("description spans multiple lines; the flat parser keeps only one")
    else:
        ok(f"description is a single line ({len(description)} chars)")
    if not body.strip():
        bad("SKILL.md body is empty; the skill would be dropped")
    else:
        ok(f"body has {len(body.strip().splitlines())} lines")
    refs = target_skill.parent / "references"
    if refs.is_dir():
        files = sorted(p.name for p in refs.glob("*.md"))
        ok(f"reference files present: {', '.join(files)}")
        for expected in ("inference-rules.md", "notation.md", "strategies.md",
                         "worked-examples.md"):
            if expected not in files:
                bad(f"referenced file missing: references/{expected}")
    else:
        bad("references/ directory is missing")

duplicates = [p for p in found_skills if p.parent.name == "math-rigor"]
if len(duplicates) > 1:
    bad("a same-named skill exists in more than one root; the first one wins: "
        + ", ".join(str(p) for p in duplicates))
elif duplicates:
    ok("no same-named skill shadows this one")

print()
print("=" * 72)
print("slash commands")
print("=" * 72)
found_commands: dict[str, list[Path]] = {}
for root in COMMAND_DIRS:
    if not root.is_dir():
        notes.append(f"command root does not exist (fine if unused): {root}")
        continue
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).with_suffix("")
        name = ":".join(relative.parts).lower()
        found_commands.setdefault(name, []).append(path)

for expected in ("prove", "audit-proof"):
    paths = found_commands.get(expected)
    if not paths:
        bad(f"command /{expected} not found in {', '.join(str(d) for d in COMMAND_DIRS)}")
        continue
    path = paths[0]
    if not COMMAND_NAME_RE.match(expected):
        bad(f"command name {expected!r} violates {COMMAND_NAME_RE.pattern}")
        continue
    fields, body = read_frontmatter(path)
    if not fields.get("description") and not body.strip():
        bad(f"/{expected}: both description and body are empty; it would be dropped")
        continue
    if not body.strip():
        bad(f"/{expected}: empty body")
    mounted = fields.get("skills", "")
    if mounted:
        names = [n.strip() for n in mounted.split(",") if n.strip()]
        for mounted_name in names:
            if not any(p.parent.name == mounted_name for p in found_skills):
                bad(f"/{expected} mounts skill {mounted_name!r}, which is not installed")
            else:
                ok(f"/{expected} mounts skill {mounted_name!r}")
    placeholder = "$ARGUMENTS" in body
    ok(f"/{expected}: description on one line, body {len(body.strip().splitlines())} lines, "
       f"$ARGUMENTS {'present' if placeholder else 'MISSING'}")
    if not placeholder:
        bad(f"/{expected}: no $ARGUMENTS placeholder; arguments would be appended instead")
    if len(paths) > 1:
        bad(f"/{expected} exists in several roots; the first wins: "
            + ", ".join(str(p) for p in paths))

print()
print("=" * 72)
print("MCP registration")
print("=" * 72)
if not CONFIG.exists():
    bad(f"user config not found: {CONFIG}")
else:
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        bad(f"{CONFIG} is not valid JSON: {exc}")
        config = {}
    servers = (config.get("mcp") or {}).get("servers") or {}
    entry = servers.get("math-rigor")
    if not entry:
        bad("mcp.servers.math-rigor is not registered")
    else:
        ok(f"registered under mcp.servers.math-rigor ({len(servers)} server(s) total)")
        command = entry.get("command", "")
        args = entry.get("args") or []
        if not command:
            bad("`command` is missing (required for stdio)")
        elif not Path(command).exists():
            bad(f"`command` does not exist on disk: {command}")
        else:
            ok(f"command exists: {command}")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            bad("`args` must be an array of strings")
        else:
            target = Path(args[0]) if args else None
            if target and not target.exists():
                bad(f"server script does not exist: {target}")
            else:
                ok(f"server script exists: {target}")
        env = entry.get("env")
        if env is not None:
            if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
                bad("`env` must be an object of string values")
            else:
                ok(f"env: {env}")
        if entry.get("type") and entry["type"] != "stdio":
            bad(f"`type` is {entry['type']!r} but the entry uses `command`; stdio is inferred "
                "from `command`, and an explicit non-stdio type would break it")
        timeout = entry.get("timeoutMs")
        if isinstance(timeout, int) and timeout >= 60000:
            ok(f"timeoutMs = {timeout} (residue-split proofs can take a minute or more)")
        else:
            notes.append(f"timeoutMs is {timeout!r}; consider >= 60000 so long proofs "
                         "are not cut off")

print()
if notes:
    print("notes:")
    for note in notes:
        print(f"  - {note}")
    print()
if problems:
    print(f"{len(problems)} problem(s) found")
    sys.exit(1)
print("installation checks passed")
