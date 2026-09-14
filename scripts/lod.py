#!/usr/bin/env python3
"""Batch-generate LOD0 (unmodified original) through LOD3 files at decreasing triangle counts
from one input file, one output file per level.

Usage:
  python3 scripts/lod.py model.glb --out-dir lods/
  python3 scripts/lod.py model.glb --out-dir lods/ --target-triangles 5000
  python3 scripts/lod.py model.glb --out-dir lods/ --ratios 0.6,0.3,0.15
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

# Matches info.py's own default LOD1/2/3 ratio suggestions (RM-010): 50%/25%/10% of the
# original triangle count.
_DEFAULT_RATIOS = (0.5, 0.25, 0.1)

_PRIMARY_EXT = {fmt: exts[0] for fmt, exts in _formats.FORMATS.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out-dir", required=True, help="directory to write LOD0.ext..LOD3.ext into (created if missing)")
    ap.add_argument("--format", help="output format for the LOD files (default: same as the input format)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--target-triangles", type=int, metavar="N", help="LOD1's absolute triangle target; LOD2/LOD3 automatically keep the same halving relationship as the default ratios")
    group.add_argument("--ratios", metavar="R1,R2,R3", help="explicit LOD1,LOD2,LOD3 triangle-fraction ratios (each in (0,1]), comma-separated; default 0.5,0.25,0.1")
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
            raise SkillError("--format blend has no triangle-ratio decimate concept in this skill's own export path; pick another format", kind="input")

        ratios = None
        if args.ratios:
            try:
                ratios = [float(x) for x in args.ratios.split(",")]
            except ValueError:
                raise SkillError(f"--ratios must be 3 comma-separated numbers, got {args.ratios!r}", kind="input")
            if len(ratios) != 3 or any(not (0 < r <= 1) for r in ratios):
                raise SkillError("--ratios needs exactly 3 values, each in (0, 1]", kind="input")
        elif not args.target_triangles:
            ratios = list(_DEFAULT_RATIOS)
        # else: ratios stays None -- resolved inside bpy from the file's own real triangle count.

        bpy_args = {
            "path": str(in_path.resolve()), "format": in_fmt,
            "output_dir": str(Path(args.out_dir).resolve()), "output_format": out_fmt,
            "output_ext": _PRIMARY_EXT[out_fmt],
            "ratios": ratios, "target_triangles": args.target_triangles,
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("lod.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"{args.input} -> {len(data['levels'])} LOD file(s) in {args.out_dir} (original: {data['triangles_original']} triangles)")
        for lvl in data["levels"]:
            print(f"  {lvl['name']}: {lvl['triangles_after']} triangles (ratio {lvl['ratio']:.2f}) -> {lvl['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
