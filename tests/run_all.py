"""Run every test module in this directory and summarise the result."""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

MODULES = [
    "test_parse.py",
    "test_translate.py",
    "test_smt.py",
    "test_logic.py",
    "test_symbolic.py",
    "test_verify.py",
    "test_proof.py",
    "test_mcp_stdio.py",
]

failures = []
for module in MODULES:
    path = os.path.join(HERE, module)
    print(f"\n{'=' * 72}\n{module}\n{'=' * 72}")
    completed = subprocess.run([PY, path], capture_output=True, text=True, timeout=1800)
    tail = (completed.stdout or "").strip().splitlines()
    for line in tail[-14:]:
        print("   " + line)
    if completed.returncode != 0:
        failures.append(module)
        if completed.stderr:
            print("   STDERR:")
            for line in completed.stderr.strip().splitlines()[-12:]:
                print("     " + line)

print(f"\n{'=' * 72}")
if failures:
    print("FAILED modules:", ", ".join(failures))
    sys.exit(1)
print("all modules passed")
