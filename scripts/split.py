#!/usr/bin/env python3
"""Split one multi-object 3D file into one output file per independent object hierarchy (a
root object with no parent, plus every descendant -- e.g. an armature and the mesh skinned to
it stay together; an unrelated standalone object becomes its own file).

Usage:
  python3 scripts/split.py model.glb --out-dir split/
  python3 scripts/split.py model.glb --out-dir split/ --format obj
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

# format -> its own primary extension, for naming each split-out file (matches _formats.FORMATS'
# own first-listed extension per format).
_PRIMARY_EXT = {fmt: exts[0] for fmt, exts in _formats.FORMATS.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out-dir", required=True, help="directory to write one file per object hierarchy into (created if missing)")
    ap.add_argument("--format", help="output format for the split files (default: same as the input format)")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    try:
        in_path = _common.require_exists(args.input, "input")
        in_fmt = _formats.detect_format(str(in_path))
        if in_fmt is None:
            raise SkillError(f"unrecognized input extension: {in_path.suffix}", kind="input")
        out_fmt = args.format or in_fmt
        if out_fmt not in _formats.FORMATS:
            raise SkillError(f"unrecognized --format: {out_fmt!r} (known: {', '.join(sorted(_formats.FORMATS))})", kind="input")
        if out_fmt == "blend":
            raise SkillError("--format blend has no per-object selection export; pick another format", kind="input")

        bpy_args = {
            "path": str(in_path.resolve()), "format": in_fmt,
            "output_dir": str(Path(args.out_dir).resolve()), "output_format": out_fmt,
            "output_ext": _PRIMARY_EXT[out_fmt],
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("split.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"{args.input} -> {len(data['groups'])} file(s) in {args.out_dir}")
        for g in data["groups"]:
            print(f"  {g['root_object']} ({len(g['objects'])} object(s)) -> {g['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
