#!/usr/bin/env python3
"""Detect-then-delegate to external CLIs this skill deliberately does not reimplement:
gltf-transform (Draco/meshopt geometry compression) and the KTX-Software `ktx` CLI (KTX2/Basis
texture compression, which gltf-transform itself shells out to). Both are optional: if a tool
isn't installed, callers get a clear SkillError(kind="missing_tool") naming how to install it,
never a bare subprocess traceback.

This is the host side only -- neither tool touches Blender; they operate directly on an
already-exported glb/gltf file.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional

from _common import SkillError

GLTF_TRANSFORM_INSTALL = "npm install -g @gltf-transform/cli"
KTX_INSTALL = "install KTX-Software (provides the `ktx` CLI): https://github.com/KhronosGroup/KTX-Software/releases"


def gltf_transform_available() -> bool:
    return shutil.which("gltf-transform") is not None


def ktx_available() -> bool:
    return shutil.which("ktx") is not None


def run_gltf_transform(subcommand: str, input_path: str, output_path: str,
                        extra_args: Optional[List[str]] = None, timeout: float = 300.0) -> str:
    """Run `gltf-transform <subcommand> input output [extra_args...]`, in place if input ==
    output. Raises SkillError(kind="missing_tool") if gltf-transform isn't on PATH, or
    kind="internal" if the subprocess itself fails (a KTX2 command's own missing `ktx`
    dependency surfaces here with gltf-transform's own error text, not a generic wrapper
    message -- see ktx_available() to check for that specific case up front instead)."""
    if not gltf_transform_available():
        raise SkillError(f"gltf-transform not found on PATH ({GLTF_TRANSFORM_INSTALL})", kind="missing_tool")
    cmd = ["gltf-transform", subcommand, input_path, output_path] + (extra_args or [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SkillError(f"gltf-transform {subcommand} did not finish within {timeout:.0f}s", kind="timeout") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise SkillError(f"gltf-transform {subcommand} failed: {' '.join(tail[-3:])}", kind="internal")
    return proc.stdout
