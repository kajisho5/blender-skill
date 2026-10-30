#!/usr/bin/env python3
"""Render a 3D file: a thumbnail, a turntable animation, or a 4-view sheet.

Usage:
  python3 scripts/render.py model.glb -o thumb.png
  python3 scripts/render.py model.glb --turntable -o turntable.mp4 --frames 48
  python3 scripts/render.py model.glb --turntable -o 'turntable_####.png' --frames 24
  python3 scripts/render.py model.glb --sheet -o sheet.png
  python3 scripts/render.py model.glb --turntable --transparent -o turntable.mov
  python3 scripts/render.py model.glb --turntable --transparent -o 'turntable_####.png'
  python3 scripts/render.py model.glb --turntable --resolution-preset vertical -o reel.mp4

--resolution-preset NAME picks a named frame size: thumbnail 512x512 (the default), square
1080x1080, 720p 1280x720, 1080p 1920x1080, 4k 3840x2160, vertical 1080x1920. An explicit
--resolution W H wins over a preset. The result's "resolution" block says which was used.

--transparent renders with alpha for compositing: an RGBA PNG (still or sequence) or a ProRes
4444 .mov. A format without alpha (.mp4/H.264, .jpg) is refused. The result's "alpha" block is
read back from the written output; a PNG sequence also carries a "sequence" block shaped for
ffmpeg-skill's sequence.py (--dir/--pattern/--fps/--start-number).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _formats
import _render_output
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
    ap.add_argument("--resolution", type=int, nargs=2, metavar=("W", "H"),
                    help="explicit frame size in pixels; wins over --resolution-preset (default 512 512)")
    ap.add_argument("--resolution-preset", metavar="NAME",
                    help="named frame size: " + ", ".join(f"{k} {w}x{h}" for k, (w, h) in _render_output.RESOLUTION_PRESETS.items()))
    ap.add_argument("--cycles", action="store_true", help="render with Cycles instead of the Eevee default")
    ap.add_argument("--light", choices=["studio", "outdoor"], default="outdoor")
    ap.add_argument("--samples", type=int, help="override the sample count (default 64, or 16 with --fast)")
    ap.add_argument("--transparent", action="store_true",
                    help="render with alpha: RGBA PNG (still or sequence) or ProRes 4444 .mov; refuses .mp4")
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

        resolution = _render_output.resolve_resolution(args.resolution_preset, args.resolution)
        output = str(Path(args.out).resolve())
        settings = _render_output.output_settings(mode, output, transparent=args.transparent,
                                                  fps=args.fps, frames=args.frames)
        bpy_args = {
            "path": str(path.resolve()), "format": fmt, "output": output,
            "mode": mode, "frames": args.frames, "fps": args.fps,
            "resolution": [resolution["width"], resolution["height"]],
            "engine": "cycles" if args.cycles else "eevee", "light": args.light,
            "samples": args.samples, "fast": args.fast, "output_settings": settings,
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args, "resolution": resolution}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("render.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout, progress=args.progress)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    data["resolution"] = resolution
    if args.transparent:
        if data.get("kind") == "video":
            data["alpha"] = _render_output.video_alpha_report(data["output_path"])
        else:
            frame = data.get("sequence", {}).get("first_frame") or data.get("output_path")
            data["alpha"] = _render_output.alpha_report(frame)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"wrote {data.get('output_path', args.out)} ({mode}, {resolution['width']}x{resolution['height']})")
        if data.get("warning"):
            print(f"  warning: {data['warning']}")
        if "alpha" in data:
            alpha = data["alpha"]
            print("  alpha: " + ("verified" if alpha["verified"] else f"NOT verified ({alpha.get('reason')})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
