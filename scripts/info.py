#!/usr/bin/env python3
"""Inspect a 3D file: object/mesh/material/texture/animation counts, unit scale, bounding box,
non-manifold and duplicate-vertex counts, UV presence -- the numbers every other script in this
skill plans from. Always probe before you operate; this is the "measure" half of
inspect -> operate -> verify.

Usage:
  python3 scripts/info.py model.glb
  python3 scripts/info.py model.glb --json
  python3 scripts/info.py model.glb --compact
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _formats
import _run
from _common import SkillError


def _compact(result: dict) -> dict:
    m = result["meshes"]
    return {
        "file": result["file"],
        "objects": result["objects"]["total"],
        "triangles": m["triangles"],
        "vertices": m["vertices"],
        "materials": result["materials"]["count"],
        "textures": len(result["textures"]),
        "animations": len(result["animations"]),
        "warnings": len(result["warnings"]),
    }


def _summary(result: dict) -> str:
    m = result["meshes"]
    lines = [
        f"{result['file']}: {result['objects']['total']} object(s), "
        f"{m['count']} mesh(es), {m['triangles']} triangles, {m['vertices']} vertices",
        f"  materials: {result['materials']['count']}  textures: {len(result['textures'])}  "
        f"animations: {len(result['animations'])}",
    ]
    for w in result["warnings"]:
        lines.append(f"  warning: {w}")
    if not result["warnings"]:
        lines.append("  no warnings")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--compact", action="store_true", help="a short JSON summary instead of the full report (implies --json)")
    _common.add_common_args(ap, dry_run=False, fast=False, progress=False)
    args = ap.parse_args()
    as_json = args.json or args.compact

    try:
        path = _common.require_exists(args.file, "file")
        fmt = _formats.detect_format(str(path))
        if fmt is None:
            raise SkillError(
                f"unrecognized file extension: {path.suffix}",
                kind="input", hint=f"known extensions: {', '.join(_formats.known_extensions())}",
            )
        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("info.py", {"path": str(path.resolve()), "format": fmt},
                               blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, as_json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), as_json)

    data = result["data"]
    if args.compact:
        print(json.dumps(_compact(data), sort_keys=True))
    elif args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(_summary(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
