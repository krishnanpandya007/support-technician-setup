#!/usr/bin/env python3
"""One-command, cross-platform launcher for support-binder.

Run it directly with any Python 3.11+; it provisions a private virtual environment
next to itself (installing the package and its deps on first use) and then runs the
CLI, passing through every argument. No global install, no manual venv, identical on
Windows, macOS, and Linux:

    python tools/support-binder/run.py --kit ../your-app-harness/support-kit
    python3 tools/support-binder/run.py --non-interactive --tables bookings,refunds

Uses only the standard library so it can bootstrap from a bare interpreter. Set
SUPPORT_BINDER_NO_BOOTSTRAP=1 to skip provisioning (e.g. when deps are already present).
"""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
VENV_DIR = TOOL_DIR / ".venv"
# Bump when pyproject deps change so existing venvs re-provision automatically.
STAMP = VENV_DIR / ".support-binder-stamp"
STAMP_VERSION = "1"


def _venv_python(venv_dir: Path) -> Path:
    """Path to the interpreter inside a venv (Windows uses Scripts\\, POSIX uses bin/)."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _provision() -> Path:
    """Create the venv and install the package if needed; return the venv interpreter."""
    py = _venv_python(VENV_DIR)
    if py.exists() and STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == STAMP_VERSION:
        return py

    if not py.exists():
        print("support-binder: creating a local environment (first run only)...", file=sys.stderr)
        venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(VENV_DIR)

    print("support-binder: installing dependencies...", file=sys.stderr)
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "-e", str(TOOL_DIR)], check=True)
    STAMP.write_text(STAMP_VERSION, encoding="utf-8")
    return py


def main() -> int:
    if sys.version_info < (3, 11):
        sys.exit(f"support-binder needs Python 3.11+, but this is {sys.version.split()[0]}.")

    if os.environ.get("SUPPORT_BINDER_NO_BOOTSTRAP") == "1":
        py = sys.executable  # caller guarantees deps are importable
    else:
        try:
            py = str(_provision())
        except subprocess.CalledProcessError as e:
            sys.exit(f"support-binder: environment setup failed ({e}). "
                     f"You can install manually: pip install -e {TOOL_DIR}")

    # Inherit stdio so the interactive wizard, hidden URL prompt, and raw-key selector all
    # work; pass through the user's args verbatim.
    completed = subprocess.run([py, "-m", "support_binder", *sys.argv[1:]])
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
