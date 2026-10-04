"""Prepare the isolated Python environment used by the DSH math-rigor plugin."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"


def default_home() -> Path:
    configured = os.environ.get("DSH_HOME", "").strip()
    return ((Path(configured).expanduser() if configured else Path.home() / ".dsh") / "math-rigor").resolve()


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def interpreter_command(requested: str | None) -> str:
    candidates = [requested] if requested else (["python.exe", "py"] if os.name == "nt" else ["python3", "python"])
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if not resolved:
            continue
        launcher_args = ["-3"] if os.name == "nt" and Path(resolved).name.lower() in {"py", "py.exe"} else []
        result = subprocess.run(
            [resolved, *launcher_args, "-c", "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[-1]
    raise RuntimeError("Python 3.10+ was not found. Install Python and retry.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", help="Python 3.10+ interpreter (Windows: py -3 or python; POSIX: python3 or python)")
    parser.add_argument("--home", type=Path, help="Plugin data directory (defaults to ~/.dsh/math-rigor)")
    args = parser.parse_args()

    home = args.home.expanduser().resolve() if args.home else default_home()
    env_root = home / "venv"
    env_python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = env_root / ".requirements.sha256"
    wanted_hash = requirements_hash()

    try:
        python = interpreter_command(args.python)
        home.mkdir(parents=True, exist_ok=True)
        if env_python.is_file():
            version_probe = subprocess.run(
                [str(env_python), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if version_probe.returncode != 0:
                print("Existing venv uses Python older than 3.10; recreating it.")
                shutil.rmtree(env_root)

        if not env_python.is_file():
            print(f"Creating isolated Python environment: {env_root}")
            subprocess.run([python, "-m", "venv", str(env_root)], check=True)

        installed_hash = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        healthy = False
        if installed_hash == wanted_hash:
            health = subprocess.run(
                [str(env_python), "-c", "import sys; assert sys.version_info >= (3, 10); import mcp, sympy, z3, mpmath"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            healthy = health.returncode == 0
        if installed_hash != wanted_hash or not healthy:
            print("Installing math-rigor requirements (network access required)...")
            subprocess.run(
                [str(env_python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
                cwd=ROOT,
                check=True,
            )
            marker.write_text(wanted_hash + "\n", encoding="utf-8")

        print(f"math-rigor DSH environment is ready: {env_python}")
        print("Restart the DSH profile to load its MCP tools.")
        return 0
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
