from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str], label: str, optional: bool = False) -> int:
    if optional and shutil.which(command[0]) is None:
        print(f"[skip] {label}: {command[0]} is not installed")
        return 0
    print(f"[run] {label}: {' '.join(command)}")
    return subprocess.call(command, cwd=ROOT)


def main() -> int:
    checks = [
        ([sys.executable, "-m", "compileall", "-q", "server", "tests", "upload_server.py"], "compile", False),
        (["ruff", "check", "."], "ruff", True),
        ([sys.executable, "-m", "unittest", "discover", "-v"], "unit tests", False),
    ]
    for command, label, optional in checks:
        status = run(command, label, optional)
        if status:
            return status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
