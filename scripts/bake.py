#!/usr/bin/env python3
"""Bake a material to a texture: diffuse/normal/AO/roughness/combined, one object or several
sharing a repacked atlas (--atlas).

Usage:
  python3 scripts/bake.py model.glb --pass ao -o ao.png
  python3 scripts/bake.py model.glb --pass combined --size 2048 -o combined.png
  python3 scripts/bake.py model.glb --pass diffuse --atlas -o atlas_diffuse.png
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

PASSES = ["ao", "normal", "roughness", "diffuse", "combined"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--pass", dest="bake_pass", required=True, choices=PASSES)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--margin", type=int, default=8, help="bake margin in pixels, to avoid seams from texture filtering")
    ap.add_argument("--object", dest="objects", action="append", help="bake only this object (repeatable); default: every mesh object")
    ap.add_argument("--atlas", action="store_true", help="repack UVs across every target object into shared space and bake them all into one image")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    try:
        path = _common.require_exists(args.file, "file")
        fmt = _formats.detect_format(str(path))
        if fmt is None:
            raise SkillError(f"unrecognized file extension: {path.suffix}", kind="input")

        bpy_args = {
            "path": str(path.resolve()), "format": fmt, "output": str(Path(args.out).resolve()),
            "pass": args.bake_pass, "size": args.size, "samples": args.samples, "margin": args.margin,
            "objects": args.objects, "atlas": args.atlas,
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("bake.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        s = data["pixel_stats"]
        print(f"wrote {args.out}: {data['pass']} bake, {data['size']}px, {len(data['objects'])} object(s)")
        print(f"  pixel stats: min={s['min']:.3f} max={s['max']:.3f} mean={s['mean']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
