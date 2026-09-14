#!/usr/bin/env python3
"""Convert between 3D formats: glb/gltf/fbx/obj/stl/usd/usdz/ply/abc/.blend.

Usage:
  python3 scripts/convert.py model.fbx -o model.glb
  python3 scripts/convert.py model.fbx -o model.glb --verify
  python3 scripts/convert.py model.fbx -o model.glb --dry-run
  python3 scripts/convert.py model.blend -o model.fbx --up-axis Z --forward-axis Y

--up-axis/--forward-axis control the OUTPUT's coordinate-system convention (Blender's own
sign-prefixed vocabulary: X, Y, Z, -X, -Y, -Z), not supported for every format: glTF only
accepts up-axis Y or Z and has no forward-axis control at all (its spec is fixed Y-up); ABC and
.blend have no axis-orientation control in Blender at all.
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
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True, help="output file path; its extension picks the target format")
    ap.add_argument("--verify", action="store_true", help="re-import the output and compare object/triangle/material/animation counts against the input")
    axis_choices = ["X", "Y", "Z", "-X", "-Y", "-Z"]
    ap.add_argument("--up-axis", choices=axis_choices, help="axis that should point up in the output (glTF only accepts Y or Z -- its spec is fixed Y-up)")
    ap.add_argument("--forward-axis", choices=axis_choices, help="axis that should point forward in the output (not supported for glTF, which has no forward-axis control)")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    try:
        in_path = _common.require_exists(args.input, "input")
        in_fmt = _formats.detect_format(str(in_path))
        out_fmt = _formats.detect_format(args.out)
        if in_fmt is None:
            raise SkillError(f"unrecognized input extension: {in_path.suffix}", kind="input")
        if out_fmt is None:
            raise SkillError(f"unrecognized output extension: {Path(args.out).suffix}", kind="input")

        if args.up_axis or args.forward_axis:
            if out_fmt in ("abc", "blend"):
                raise SkillError(f"--up-axis/--forward-axis: {out_fmt!r} has no axis-orientation control in Blender", kind="input")
            if out_fmt == "gltf":
                if args.forward_axis:
                    raise SkillError("--forward-axis is not supported for glTF (it has no forward-axis control, only --up-axis Y or Z)", kind="input")
                if args.up_axis not in (None, "Y", "Z"):
                    raise SkillError(f"--up-axis for glTF must be Y or Z (its spec is fixed Y-up; Z is a non-compliant escape hatch) -- got {args.up_axis!r}", kind="input")

        bpy_args = {
            "input": str(in_path.resolve()), "input_format": in_fmt,
            "output": str(Path(args.out).resolve()), "output_format": out_fmt,
            "verify": args.verify,
            "forward_axis": args.forward_axis, "up_axis": args.up_axis,
        }
        if args.dry_run:
            cmd = _run.blender_command("convert.py", "<args.json>", "<result.json>", args.blender or "blender")
            print(json.dumps({"blender_command": cmd, "args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("convert.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        m = data["input"]["meshes"]
        print(f"{args.input} -> {args.out}  ({m['count']} mesh(es), {m['triangles']} triangles)")
        if args.verify:
            v = data["verify"]
            if v["matches"]:
                print("  verify: OK (object/triangle/material/animation counts match)")
            else:
                print("  verify: DIFFERS")
                for d in v["diffs"]:
                    print(f"    {d}")
    if args.verify and not data.get("verify", {}).get("matches", True):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
