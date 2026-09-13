#!/usr/bin/env python3
"""Shared host-side helpers: common CLI flags, JSON/text output, and typed errors.

Every scripts/*.py imports this. Keep it stdlib-only (Python 3.9+, no third-party deps) --
that guarantee is part of what this skill promises (see README "Requirements").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class SkillError(Exception):
    """A user-facing failure. `kind` is machine-readable (used by --json and by an MCP caller
    to tell "no such file" from "Blender not found" from "timed out") without regexing stderr."""

    def __init__(self, message: str, kind: str = "input", hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.kind = kind  # input | missing_tool | timeout | internal
        self.hint = hint

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


def add_common_args(ap: argparse.ArgumentParser, *, dry_run: bool = True, fast: bool = True,
                     progress: bool = True) -> None:
    """Flags shared by every writing/inspecting tool. Not every flag applies to every script
    (an inspecting tool like info.py has no --fast); pass the matching kwargs to skip it."""
    ap.add_argument("--json", action="store_true", help="print a machine-readable JSON result instead of a human-readable summary")
    ap.add_argument("--blender", help="path to the Blender executable (overrides $BLENDER and PATH search)")
    ap.add_argument("--timeout", type=float, default=1800.0, help="seconds to allow the Blender subprocess to run (default 1800)")
    if dry_run:
        ap.add_argument("--dry-run", action="store_true", help="print the Blender command and bpy script that would run, without running it")
    if fast:
        ap.add_argument("--fast", action="store_true", help="lower-quality preview instead of the final-quality result")
    if progress:
        ap.add_argument("--progress", action="store_true", help="print percent/ETA to stderr while running")


def emit(result: Dict[str, Any], as_json: bool, summary: str) -> None:
    """Print either the full JSON result or a one-line human summary; JSON always to stdout so
    `--json` output is never mixed with progress/log lines (those go to stderr)."""
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(summary)


def fail(err: SkillError, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"status": "error", "error": err.to_dict()}, indent=2, sort_keys=True))
    else:
        print(f"error: {err.message}" + (f" ({err.hint})" if err.hint else ""), file=sys.stderr)
    return 2 if err.kind == "missing_tool" else (3 if err.kind == "timeout" else 1)


def require_exists(path: str, what: str = "input") -> Path:
    p = Path(path)
    if not p.exists():
        raise SkillError(f"{what} not found: {path}", kind="input")
    return p


def output_path(input_path: Path, operation: str, ext: Optional[str] = None, out: Optional[str] = None) -> Path:
    """`<input>_<operation>.<ext>` unless -o/--out was given explicitly."""
    if out:
        return Path(out)
    suffix = ext if ext is not None else input_path.suffix.lstrip(".")
    return input_path.with_name(f"{input_path.stem}_{operation}.{suffix}")
