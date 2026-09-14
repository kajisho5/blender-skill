"""Shared bpy-side helpers: read the host's args JSON, run a function, write the result JSON.

Every scripts/bpy/*.py ends with:

    if __name__ == "__main__":
        _common.main(run)

`run(args: dict) -> dict` does the actual bpy work and returns the "data" payload; _common.main
wraps it so a Python exception anywhere in bpy (a missing importer, a corrupt file, an
unsupported feature) always becomes {"ok": false, "error": {...}} on disk instead of a Blender
crash dialog or a bare traceback the host side can't parse. Blender's own argv layout is
`blender ... -- <args_json_path> <result_json_path>` (everything after `--` is passed through
to the script via sys.argv, unfiltered by Blender's own option parsing).
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict


def _after_dashdash():
    argv = sys.argv
    if "--" not in argv:
        raise RuntimeError("expected '-- <args.json> <result.json>' on the Blender command line")
    return argv[argv.index("--") + 1:]


def read_args() -> Dict[str, Any]:
    rest = _after_dashdash()
    if len(rest) < 2:
        raise RuntimeError("expected exactly two paths after '--': args.json and result.json")
    with open(rest[0], "r", encoding="utf-8") as fh:
        return json.load(fh)


def result_path() -> str:
    return _after_dashdash()[1]


def main(run: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    out_path = result_path()
    args = read_args()
    try:
        data = run(args)
        result: Dict[str, Any] = {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001 -- must never let bpy crash without writing a result
        result = {
            "ok": False,
            "error": {"kind": "internal", "message": str(exc), "traceback": traceback.format_exc()},
        }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
