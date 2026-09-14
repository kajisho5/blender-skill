#!/usr/bin/env python3
"""Render a 3D file: a thumbnail, a turntable animation, or a 4-view sheet.

Usage:
  python3 scripts/render.py model.glb -o thumb.png
  python3 scripts/render.py model.glb --turntable -o turntable.mp4 --frames 48
  python3 scripts/render.py model.glb --turntable -o 'turntable_####.png' --frames 24
  python3 scripts/render.py model.glb --sheet -o sheet.png
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--turntable", action="store_true")
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--frames", type=int, default=24, help="turntable frame count (default 24)")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"), default=(512, 512))
    ap.add_argument("--cycles", action="store_true", help="render with Cycles instead of the Eevee default")
    ap.add_argument("--light", choices=["studio", "outdoor"], default="outdoor")
    ap.add_argument("--samples", type=int, help="override the sample count (default 64, or 16 with --fast)")
    _common.add_common_args(ap)
    args = ap.parse_args()

    if args.turntable and args.sheet:
        ap.error("give at most one of --turntable, --sheet (default is a single thumbnail)")
    mode = "turntable" if args.turntable else ("sheet" if args.sheet else "thumbnail")

    try:
        path = _common.require_exists(args.file, "file")
        fmt = _formats.detect_format(str(path))
        if fmt is None:
            raise SkillError(f"unrecognized file extension: {path.suffix}", kind="input")

        bpy_args = {
            "path": str(path.resolve()), "format": fmt, "output": str(Path(args.out).resolve()),
            "mode": mode, "frames": args.frames, "fps": args.fps, "resolution": args.resolution,
            "engine": "cycles" if args.cycles else "eevee", "light": args.light,
            "samples": args.samples, "fast": args.fast,
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("render.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout, progress=args.progress)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"wrote {data.get('output_path', args.out)} ({mode})")
        if data.get("warning"):
            print(f"  warning: {data['warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
