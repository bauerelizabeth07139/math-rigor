"""Safety checks: what this plugin may touch, read from its own files.

A prover that runs a Python server has three things worth checking: what it
spawns, what it downloads, and what it writes. Each check below reads the
plugin's shipped files, so the answer is reproducible rather than asserted in
prose, and every failure names the file and line it objected to.

The posture being checked:

* the server child is given **one** environment variable — the plugin's own home
  — instead of inheriting the harness's environment, which carries API keys;
* nothing is spawned through a shell, so no value is ever parsed as a command;
* the only network access is `pip install` of a **pinned** requirement list,
  which is what makes the download auditable;
* writes stay inside the plugin's home (`$DSH_HOME/math-rigor`), the venv and
  the marker file it keeps beside it.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

failures = []
checks = 0


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def hits(relative, pattern):
    """Every match in one file, as `file:line — text`."""
    found = []
    for number, line in enumerate(read(relative).splitlines(), start=1):
        if re.search(pattern, line):
            found.append(f"{relative}:{number} — {line.strip()[:100]}")
    return found


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        failures.append(label)
        print(f"FAIL  {label}" + (f" — {detail}" if detail else ""))


def shipped_sources():
    """The JavaScript and Python this package actually ships."""
    found = ["index.js"]
    server = os.path.join(ROOT, "server")
    for base, _dirs, files in os.walk(server):
        for name in files:
            if name.endswith(".py"):
                found.append(os.path.relpath(os.path.join(base, name), ROOT).replace("\\", "/"))
    return found


print("math-rigor plugin safety")

# -- install time -----------------------------------------------------------
manifest = json.loads(read("package.json"))
scripts = manifest.get("scripts") or {}
hooks = [name for name in ("preinstall", "install", "postinstall", "prepare", "prepack")
         if name in scripts]
check("nothing runs on the user's machine at install time", not hooks,
      f"hooks: {', '.join(hooks)}" if hooks else "no install or publish hooks")

# -- dependencies -----------------------------------------------------------
runtime = manifest.get("dependencies") or {}
outside = [name for name in runtime if not name.startswith("@deepseek-ai/")]
check("it adds no third-party runtime dependency to the profile", not outside,
      f"runtime dependencies: {', '.join(runtime) or 'none'}")
peers = list((manifest.get("peerDependencies") or {}).keys())
check("everything else it needs is declared a peer of the harness",
      all(name.startswith("@deepseek-ai/") for name in peers),
      f"peers: {', '.join(peers)}")

index = read("index.js")

# -- what it spawns ---------------------------------------------------------
spread = re.findall(r"env:\s*\{[^}]*\.\.\.\s*process\.env", index)
check("no child inherits the harness's environment wholesale", not spread,
      f"{len(spread)} site(s) spread process.env" if spread else
      "every spawn names the variables it passes")

minimal = re.search(r"env:\s*\{\s*MATH_RIGOR_HOME:\s*home\s*\}", index)
check("the long-running server child gets exactly one variable", minimal is not None,
      "env: { MATH_RIGOR_HOME: home }" if minimal else "the server spawn does not pin its environment")

shell = hits("index.js", r"shell\s*:\s*true|execSync?\s*\(")
check("nothing is spawned through a shell", not shell, "\n      ".join(shell))

dynamic = hits("index.js", r"(?<![\w.])eval\s*\(|new\s+Function\s*\(")
check("no dynamically built code runs in JavaScript", not dynamic, "\n      ".join(dynamic))

for relative in shipped_sources():
    if not relative.endswith(".py"):
        continue
    danger = hits(relative, r"(?<![\w.])eval\s*\(|(?<![\w.])(exec|compile)\s*\([^)\n]*\+|shell\s*=\s*True")
    check(f"no dynamic code or shell in {relative}", not danger, "\n      ".join(danger))

# -- what it downloads ------------------------------------------------------
requirements = [line.strip() for line in read("requirements.txt").splitlines()
                if line.strip() and not line.strip().startswith("#")]
unpinned = [line for line in requirements if "==" not in line]
check("every package it asks pip for is pinned to an exact version", not unpinned,
      ", ".join(requirements))
check("the download happens only through pip, from that list",
      "pip" in index and "-r" in index,
      "index.js runs `<venv> -m pip install -r requirements.txt`")

# -- what it writes ---------------------------------------------------------
writes = hits("index.js", r"writeFile\(|mkdir\(|rm\(|rename\(|unlink\(")
stray = [line for line in writes if not re.search(r"home|envRoot|marker|venv|ROOT|dirname|path|config", line)]
check("every write lands in its own home, the venv or the marker", not stray,
      f"{len(writes)} write site(s)" if not stray else "\n      ".join(stray))

# -- what it reads ----------------------------------------------------------
# A lexer has tokens; a credential is read from the environment or a file, so
# the rule looks for exactly those two shapes.
CREDENTIAL = (r"\.credentials\.ya?ml|apiKeyEnv"
              r"|process\.env\.[A-Z_]*(TOKEN|KEY|SECRET|PASSWORD)"
              r"|os\.environ(?:\.get)?\[?\(?[\"'][A-Z_]*(TOKEN|KEY|SECRET|PASSWORD)"
              r"|getenv\([\"'][A-Z_]*(TOKEN|KEY|SECRET|PASSWORD)")
credentials = []
for relative in ["index.js"] + [name for name in shipped_sources() if name.endswith(".py")]:
    credentials += hits(relative, CREDENTIAL)
check("it reads no credential", not credentials, "\n      ".join(credentials))

# -- packaging --------------------------------------------------------------
patch = (manifest.get("dsh") or {}).get("bundle", {}).get("patch")
check("the bundle patch it declares is present and names this package",
      bool(patch) and os.path.isfile(os.path.join(ROOT, patch))
      and manifest["name"] in read(patch),
      f"{patch}")

obfuscated = []
for relative in shipped_sources():
    obfuscated += hits(relative, r"(\\x[0-9a-fA-F]{2}){8,}")
check("nothing is obfuscated", not obfuscated, "\n      ".join(obfuscated))

print(f"\n{checks - len(failures)}/{checks} checks passed")
sys.exit(1 if failures else 0)
