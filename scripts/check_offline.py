"""The same offline Python gate used locally and in CI. Never calls live API."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for args in (
    ["-m", "ruff", "check", "apps/api", "packages", "scripts", "tests", "evals", "migrations"],
    [
        "-m",
        "ruff",
        "format",
        "--check",
        "apps/api",
        "packages",
        "scripts",
        "tests",
        "evals",
        "migrations",
    ],
    ["-m", "unittest", "discover", "-s", "tests", "-v"],
    ["scripts/validate_demo.py"],
    ["scripts/agent_smoke.py"],
):
    result = subprocess.run([sys.executable, *args], cwd=root)
    if result.returncode:
        sys.exit(result.returncode)
