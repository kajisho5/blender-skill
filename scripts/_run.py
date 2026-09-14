#!/usr/bin/env python3
"""Find and launch Blender headless, exchanging arguments/results as JSON files rather than
parsing stdout (Blender's own log lines are unstructured and version-dependent).

Every scripts/*.py that touches a 3D file calls run_bpy(): it writes `args` to a temp JSON
file, runs `blender -b --factory-startup --python scripts/bpy/<name>.py -- <args-json> <result-json>`,
and reads the result JSON that the bpy-side script wrote. stdout/stderr from Blender itself are
only used for the error message when something goes wrong (nothing about a successful call is
parsed from them).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import SkillError

BPY_DIR = Path(__file__).resolve().parent / "bpy"

_INSTALL_HINT = {
    "Darwin": "brew install --cask blender, or download from https://www.blender.org/download/ (installs to /Applications/Blender.app)",
    "Windows": "winget install BlenderFoundation.Blender, or download from https://www.blender.org/download/",
    "Linux": "download the official tarball from https://www.blender.org/download/ (apt's blender package is often outdated), or your distro's snap/flatpak",
}


def _os_default_paths() -> List[str]:
    system = platform.system()
    if system == "Darwin":
        return ["/Applications/Blender.app/Contents/MacOS/Blender"]
    if system == "Windows":
        import glob
        return sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"), reverse=True)
    # Linux: apt package, Steam, snap, flatpak wrapper locations.
    return [
        "/usr/bin/blender",
        "/snap/bin/blender",
        "/var/lib/flatpak/exports/bin/org.blender.Blender",
        str(Path.home() / ".local/share/flatpak/exports/bin/org.blender.Blender"),
    ]


def find_blender(explicit: Optional[str] = None) -> str:
    """--blender argument -> $BLENDER -> PATH -> OS-standard install locations."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("BLENDER")
    if env:
        candidates.append(env)
    on_path = shutil.which("blender")
    if on_path:
        candidates.append(on_path)
    candidates += _os_default_paths()
    for c in candidates:
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return c
    hint = _INSTALL_HINT.get(platform.system(), "see https://www.blender.org/download/")
    raise SkillError(
        "Blender was not found (checked --blender, $BLENDER, PATH, and the usual install locations).",
        kind="missing_tool", hint=hint,
    )


def blender_command(bpy_script: str, args_path: str, result_path: str, blender_bin: str,
                     extra_args: Optional[List[str]] = None) -> List[str]:
    cmd = [blender_bin, "-b", "--factory-startup", "--python", str(BPY_DIR / bpy_script), "--"]
    if extra_args:
        cmd += extra_args
    cmd += [args_path, result_path]
    return cmd


def run_bpy(bpy_script: str, args: Dict[str, Any], *, blender_bin: Optional[str] = None,
            timeout: float = 1800.0, extra_args: Optional[List[str]] = None,
            progress: bool = False) -> Dict[str, Any]:
    """Run scripts/bpy/<bpy_script> with `args`, return its result JSON.

    Raises SkillError(kind="timeout") if Blender doesn't finish in `timeout` seconds, or
    SkillError(kind="internal") if it exits non-zero or writes no result file (the bpy side
    catches its own exceptions and always writes {"ok": false, "error": ...} -- a missing
    result file means Blender itself crashed, e.g. a segfault or an addon failing to load).
    """
    blender_bin = blender_bin or find_blender()
    with tempfile.TemporaryDirectory(prefix="blender-skill-") as tmp:
        args_path = str(Path(tmp) / "args.json")
        result_path = str(Path(tmp) / "result.json")
        Path(args_path).write_text(json.dumps(args), encoding="utf-8")
        cmd = blender_command(bpy_script, args_path, result_path, blender_bin, extra_args)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SkillError(f"Blender did not finish within {timeout:.0f}s", kind="timeout") from exc
        if progress and proc.stderr:
            print(proc.stderr, file=sys.stderr)
        result_file = Path(result_path)
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SkillError(f"Blender wrote an unreadable result file: {exc}", kind="internal") from exc
            if not result.get("ok", False) and "error" not in result:
                result["error"] = {"kind": "internal", "message": "bpy script reported failure with no error detail"}
            return result
        tail = "\n".join((proc.stderr or "").splitlines()[-25:])
        raise SkillError(
            f"Blender exited {proc.returncode} without writing a result (no crash-safe error available)",
            kind="internal", hint=tail or None,
        )
