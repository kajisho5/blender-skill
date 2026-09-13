#!/usr/bin/env python3
"""Assemble a declarative scene.json (asset placement, lights, camera, background, output)
into a render and/or an exported 3D file.

Usage:
  python3 scripts/scene.py --init scene.json
  python3 scripts/scene.py scene.json
  python3 scripts/scene.py scene.json -o out.png
  python3 scripts/scene.py scene.json --export out.glb
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

TEMPLATE = {
    "assets": [
        {"file": "model.glb", "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1.0},
    ],
    "lights": [
        {"type": "sun", "position": [0, 0, 3], "rotation": [45, 0, 45], "energy": 3.0},
    ],
    "camera": {"position": [4, -4, 3], "look_at": [0, 0, 0], "lens": 50},
    "background": {"color": [0.05, 0.05, 0.05], "strength": 1.0},
    "output": {"path": "scene_render.png", "resolution": [1024, 1024], "engine": "eevee", "samples": 64},
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", nargs="?", help="scene.json to assemble")
    ap.add_argument("--init", metavar="FILE", help="write a template scene.json and exit")
    ap.add_argument("-o", "--out", help="override output.path from the scene file")
    ap.add_argument("--export", metavar="FILE", help="also export the assembled scene as a 3D file")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    if args.init:
        Path(args.init).write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
        print(f"wrote template {args.init}")
        return 0

    if not args.scene:
        ap.error("give a scene.json, or --init FILE to create one")

    try:
        scene_path = _common.require_exists(args.scene, "scene.json")
        spec = json.loads(scene_path.read_text(encoding="utf-8"))

        bpy_args = {"scene": spec, "scene_dir": str(scene_path.resolve().parent), "output_override": args.out}
        if args.export:
            export_fmt = _formats.detect_format(args.export)
            if export_fmt is None:
                raise SkillError(f"unrecognized export extension: {Path(args.export).suffix}", kind="input")
            bpy_args["export"] = str(Path(args.export).resolve())
            bpy_args["export_format"] = export_fmt

        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("scene.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"assembled {args.scene}: {data['assets_placed']} object(s) placed")
        if "rendered" in data:
            print(f"  rendered -> {data['rendered']}")
        if "exported" in data:
            print(f"  exported -> {data['exported']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
