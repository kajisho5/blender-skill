#!/usr/bin/env python3
"""Reduce a 3D file's cost: decimate, weld duplicate vertices, recalc normals, triangulate,
cap texture resolution, purge unused data blocks. Never overwrites the input.

--draco/--meshopt/--ktx2 delegate to gltf-transform (and, for --ktx2, the KTX-Software `ktx`
CLI) as a post-process on the glb/gltf this command just wrote -- this skill does not
reimplement those compressors. Each is a detect-then-delegate: if the tool isn't installed, the
result says so with an install command rather than failing the whole run (everything Blender did
is already saved).

Usage:
  python3 scripts/optimize.py model.glb -o model_optimized.glb --decimate-ratio 0.5
  python3 scripts/optimize.py model.glb -o model_optimized.glb --target-mobile
  python3 scripts/optimize.py model.glb -o model_optimized.glb --weld-doubles --triangulate --purge-unused
  python3 scripts/optimize.py model.glb -o model_optimized.glb --draco
  python3 scripts/optimize.py model.glb -o model_optimized.glb --ktx2
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _delegate
import _formats
import _run
from _common import SkillError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--decimate-ratio", type=float, help="0-1, fraction of triangles to keep")
    ap.add_argument("--weld-doubles", action="store_true", help="merge vertices at the same position (dist 1e-5)")
    ap.add_argument("--recalc-normals", action="store_true", help="recalculate outside-facing normals")
    ap.add_argument("--triangulate", action="store_true")
    ap.add_argument("--texture-max", type=int, metavar="PX", help="downscale any texture wider/taller than this")
    ap.add_argument("--purge-unused", action="store_true", help="remove orphan data blocks (unused meshes/materials/images/actions) after other operations")
    ap.add_argument("--target-web", action="store_const", dest="target", const="web")
    ap.add_argument("--target-mobile", action="store_const", dest="target", const="mobile")
    ap.add_argument("--target-ar", action="store_const", dest="target", const="ar")
    ap.add_argument("--draco", action="store_true", help="compress geometry with Draco (delegates to gltf-transform; glb/gltf output only)")
    ap.add_argument("--meshopt", action="store_true", help="compress geometry/animation with Meshopt (delegates to gltf-transform; glb/gltf output only; Blender itself cannot re-import the result -- see references/pitfalls.md)")
    ap.add_argument("--ktx2", action="store_true", help="compress textures to KTX2/Basis (delegates to gltf-transform + the KTX-Software `ktx` CLI; glb/gltf output only)")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    if args.draco and args.meshopt:
        ap.error("--draco and --meshopt are alternative geometry compressors -- give at most one")

    try:
        in_path = _common.require_exists(args.input, "input")
        in_fmt = _formats.detect_format(str(in_path))
        out_fmt = _formats.detect_format(args.out)
        if in_fmt is None or out_fmt is None:
            raise SkillError("unrecognized file extension on input or output", kind="input")
        if (args.draco or args.meshopt or args.ktx2) and out_fmt != "gltf":
            raise SkillError("--draco/--meshopt/--ktx2 need a glb/gltf output (gltf-transform doesn't operate on other formats)", kind="input")

        bpy_args = {
            "path": str(in_path.resolve()), "format": in_fmt,
            "output": str(Path(args.out).resolve()), "output_format": out_fmt,
            "decimate_ratio": args.decimate_ratio, "weld_doubles": args.weld_doubles,
            "recalc_normals": args.recalc_normals, "triangulate": args.triangulate,
            "texture_max": args.texture_max, "purge_unused": args.purge_unused,
            "target": args.target,
        }
        if args.dry_run:
            print(json.dumps({"args": bpy_args}, indent=2))
            return 0

        blender_bin = _run.find_blender(args.blender)
        result = _run.run_bpy("optimize.py", bpy_args, blender_bin=blender_bin, timeout=args.timeout)
    except SkillError as err:
        return _common.fail(err, args.json)

    if not result.get("ok"):
        return _common.fail(SkillError(result["error"]["message"], kind=result["error"].get("kind", "internal")), args.json)

    data = result["data"]
    data["delegated"] = []
    out_str = str(Path(args.out).resolve())
    if args.draco or args.meshopt:
        subcommand = "draco" if args.draco else "meshopt"
        if not _delegate.gltf_transform_available():
            data["delegated"].append({"tool": subcommand, "ran": False, "reason": f"gltf-transform not found ({_delegate.GLTF_TRANSFORM_INSTALL})"})
        else:
            try:
                _delegate.run_gltf_transform(subcommand, out_str, out_str)
                data["delegated"].append({"tool": subcommand, "ran": True})
            except SkillError as err:
                data["delegated"].append({"tool": subcommand, "ran": False, "reason": err.message})
    if args.ktx2:
        if not _delegate.gltf_transform_available():
            data["delegated"].append({"tool": "ktx2", "ran": False, "reason": f"gltf-transform not found ({_delegate.GLTF_TRANSFORM_INSTALL})"})
        elif not _delegate.ktx_available():
            data["delegated"].append({"tool": "ktx2", "ran": False, "reason": f"the `ktx` CLI was not found ({_delegate.KTX_INSTALL})"})
        else:
            try:
                _delegate.run_gltf_transform("uastc", out_str, out_str)
                data["delegated"].append({"tool": "ktx2", "ran": True})
            except SkillError as err:
                data["delegated"].append({"tool": "ktx2", "ran": False, "reason": err.message})

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        pct = (1 - data["triangles_after"] / data["triangles_before"]) * 100 if data["triangles_before"] else 0
        print(f"{args.input} -> {args.out}")
        print(f"  triangles: {data['triangles_before']} -> {data['triangles_after']} ({pct:.0f}% reduction)")
        if data["vertices_welded"]:
            print(f"  welded {data['vertices_welded']} duplicate vertices")
        for t in data["textures_resized"]:
            print(f"  texture {t['name']}: {t['from']} -> {t['to']}")
        if data["orphan_data_purged"]:
            print(f"  purged {data['orphan_data_purged']} orphan data block(s)")
        for d in data["delegated"]:
            print(f"  {d['tool']}: ok" if d["ran"] else f"  {d['tool']}: skipped ({d['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
