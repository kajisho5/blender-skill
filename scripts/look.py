#!/usr/bin/env python3
"""The agent's eyes: render things a JSON probe can't tell you.

Usage:
  python3 scripts/look.py model.glb --uv -o uv.svg
  python3 scripts/look.py model.glb --wireframe -o wire.png
  python3 scripts/look.py model.glb --textures -o textures.png
  python3 scripts/look.py --compare before.png after.png -o compare.png
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
    ap.add_argument("file", nargs="?", help="a 3D file (not used with --compare)")
    ap.add_argument("--uv", action="store_true", help="UV layout as SVG (headless Blender can't rasterize this to PNG -- see references/pitfalls.md)")
    ap.add_argument("--wireframe", action="store_true", help="a render with every mesh shown as wireframe")
    ap.add_argument("--textures", action="store_true", help="a grid of every texture used in the file")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="two existing images placed side by side, with a PSNR/SSIM similarity score when they share pixel dimensions")
    ap.add_argument("-o", "--out", required=True)
    _common.add_common_args(ap, dry_run=False, progress=False)
    args = ap.parse_args()

    modes = [m for m in ("uv", "wireframe", "textures") if getattr(args, m)]
    if args.compare:
        modes.append("compare")
    if len(modes) != 1:
        ap.error("give exactly one of --uv, --wireframe, --textures, --compare")
    mode = modes[0]

    try:
        if mode == "compare":
            bpy_args = {"mode": "compare", "image_a": str(_common.require_exists(args.compare[0], "image").resolve()),
                        "image_b": str(_common.require_exists(args.compare[1], "image").resolve()),
                        "output": str(Path(args.out).resolve())}
        else:
            if not args.file:
                ap.error("a 3D file is required for --uv/--wireframe/--textures")
            path = _common.require_exists(args.file, "file")
            fmt = _formats.detect_format(str(path))
            if fmt is None:
                raise SkillError(f"unrecognized file extension: {path.suffix}", kind="input")
            bpy_args = {"mode": mode, "path": str(path.resolve()), "format": fmt,
                        "output": str(Path(args.out).resolve())}
            if args.fast:
                bpy_args["samples"] = 8

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("look.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif "skipped" in data:
        print(f"skipped: {data['skipped']}")
    else:
        print(f"wrote {args.out} ({mode})")
        if "psnr" in data:
            print(f"  psnr: {data['psnr']:.2f} dB, ssim: {data['ssim']:.4f}")
        elif "score_skipped" in data:
            print(f"  score: skipped ({data['score_skipped']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
