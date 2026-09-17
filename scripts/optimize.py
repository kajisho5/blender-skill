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
  python3 scripts/optimize.py model.glb -o model_optimized.glb --fill-holes
  python3 scripts/optimize.py model.glb -o model_optimized.glb --fix-scale
  python3 scripts/optimize.py model.glb -o model_optimized.glb --origin bottom
  python3 scripts/optimize.py model.glb -o model_optimized.glb --draco
  python3 scripts/optimize.py model.glb -o model_optimized.glb --ktx2
  python3 scripts/optimize.py model.glb -o model_optimized.glb --keyframe-decimate 0.01
  python3 scripts/optimize.py model.glb -o model_optimized.glb --remove-unused-shape-keys
  python3 scripts/optimize.py model.glb -o model_optimized.glb --instance-duplicate-meshes
  python3 scripts/optimize.py model.glb -o model_optimized.glb --merge-materials
  python3 scripts/optimize.py model.glb -o model_optimized.glb --target-vrchat
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _delegate
import _formats
import _gltf_extensions
import _run
from _common import SkillError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--decimate-ratio", type=float, help="0-1, fraction of triangles to keep")
    ap.add_argument("--fill-holes", action="store_true", help="fill boundary-edge holes/gaps (see info.py's non-manifold warning); does not touch non-manifold edges shared by 3+ faces")
    ap.add_argument("--fix-scale", action="store_true", help="bake unapplied object scale into the mesh (a common cm/m unit-mismatch symptom -- see info.py's scale warning); world-space size is unchanged")
    ap.add_argument("--origin", choices=["center", "bottom", "keep"], help="move each object's origin to its bounding-box center or bottom-center, without moving the geometry in world space; default keep")
    ap.add_argument("--weld-doubles", action="store_true", help="merge vertices at the same position (dist 1e-5)")
    ap.add_argument("--recalc-normals", action="store_true", help="recalculate outside-facing normals")
    ap.add_argument("--triangulate", action="store_true")
    ap.add_argument("--texture-max", type=int, metavar="PX", help="downscale any texture wider/taller than this")
    ap.add_argument("--texture-auto-resolution", action="store_true", help="downscale each texture to its own computed cap instead of one flat --texture-max: derived from how much of the whole file's combined bounding box the largest object using that texture spans (screen-occupancy proxy) times --viewport-width, rounded to the next power of two. Rejected together with --texture-max.")
    ap.add_argument("--viewport-width", type=int, metavar="PX", default=1920, help="assumed viewport width for --texture-auto-resolution (default 1920)")
    ap.add_argument("--purge-unused", action="store_true", help="remove orphan data blocks (unused meshes/materials/images/actions) after other operations")
    ap.add_argument("--point-thin-voxel", type=float, metavar="SIZE", help="thin a point cloud (a vertices-only mesh, e.g. from PLY scan data) by voxel-grid downsampling: keep one point per SIZE-sized grid cell. No-op on any mesh that has faces -- use --decimate-ratio for those.")
    ap.add_argument("--webp", action="store_true", help="convert every texture to WebP on export (glb/gltf output only; adds the EXT_texture_webp extension). Not always smaller -- WebP at default quality can exceed a well-compressed PNG for some textures; the reported before/after byte counts are measured, never assumed.")
    ap.add_argument("--webp-quality", type=int, metavar="0-100", help="WebP encode quality (default 75, Blender's own default); only used with --webp")
    ap.add_argument("--fix-colorspace", action="store_true", help="correct every texture colorspace mismatch info.py flags (Base Color/Emission not 'sRGB'; Metallic/Roughness/Alpha/a normal map's texture not 'Non-Color') based on which material socket it feeds")
    ap.add_argument("--texture-colorspace", metavar="NAME", help="force every texture's colorspace tag to NAME regardless of role (e.g. 'ACEScg', 'ACES2065-1', 'sRGB', 'Non-Color') -- a blunt override, not per-socket correction; see --fix-colorspace for that. Rejected together with --fix-colorspace.")
    ap.add_argument("--remove-unused-bones", action="store_true", help="prune every bone with zero skin-weight influence and no animation, from the leaves inward -- never touches a bone a still-used descendant needs, or one that's animated")
    ap.add_argument("--max-bones", type=int, metavar="N", help="reduce each armature to at most N bones (a real budget cap, e.g. for mobile skinning limits): removes the lowest-influence unanimated leaf bones first (including genuinely unused ones, for free), transferring each one's skin weight to its parent. Never removes an animated bone -- warns instead if the cap can't be reached without one.")
    ap.add_argument("--keyframe-decimate", type=float, metavar="TOLERANCE", help="reduce every action's keyframe count via Ramer-Douglas-Peucker curve simplification: a keyframe is dropped only if its own interpolation mode's real curve (LINEAR or CONSTANT only -- the two modes where the post-removal value is exactly computable) would deviate by at most TOLERANCE from its real value (in that fcurve's own units -- Blender units for location, radians for rotation, unitless for scale). A BEZIER-governed keyframe (Blender's own default for a hand-keyed action) or any other interpolation is never removed -- a glTF import is exclusively LINEAR, so this is not a hobbled feature for that common case. The first/last keyframe of every fcurve always survives. Must be a finite number > 0.")
    ap.add_argument("--remove-unused-shape-keys", action="store_true", help="remove every non-Basis shape key whose maximum per-vertex displacement from whatever it's actually defined relative to (usually Basis, but a shape key can be defined relative to another shape key) is below a tiny fixed epsilon (1e-5, this file's own weld-doubles vertex-position precision) -- a shape key that geometrically does nothing no matter its value slider, mute state, or any driver pointed at it.")
    ap.add_argument("--instance-duplicate-meshes", action="store_true", help="merge every group of mesh objects on separate datablocks that are fully identical (exact vertex positions/face topology/UVs/vertex colors, and the exact same Material datablocks -- not just similarly shaped, see info.py's own duplicate_mesh_candidates for that looser suggestion) onto one shared datablock, freeing the now-orphaned duplicates. Never touches a mesh with shape keys or an Armature modifier, since shared mesh data means shared shape-key/vertex-weight state in Blender's own data model.")
    ap.add_argument("--merge-materials", action="store_true", help="merge every group of separate Material datablocks that are fully identical (every material property, and -- when use_nodes is on -- the exact same node graph: same node types/settings/socket values and the same links between them, not just a similar node count) onto one canonical datablock, freeing the now-orphaned duplicates. A material referencing a shared node group (ShaderNodeGroup) only matches another using the exact same node-group datablock, not a separately-authored but structurally-identical one.")
    ap.add_argument("--target-web", action="store_const", dest="target", const="web")
    ap.add_argument("--target-mobile", action="store_const", dest="target", const="mobile")
    ap.add_argument("--target-ar", action="store_const", dest="target", const="ar")
    ap.add_argument("--target-sketchfab", action="store_const", dest="target", const="sketchfab", help="texture-max 4096 (Sketchfab's own recommendation is 1-4 4K textures), no decimation -- Sketchfab has no official triangle cap and is a display/hosting platform, not itself hardware-constrained")
    ap.add_argument("--target-vrchat", action="store_const", dest="target", const="vrchat", help="triangle budget 70,000 (VRChat's official PC 'Good' Performance Rank threshold), texture-max 2048 -- see creators.vrchat.com/avatars/avatar-performance-ranking-system; VRChat's own Quest/Android thresholds are much stricter (10,000 triangles for 'Good'), use --decimate-ratio/--texture-max directly if you need those instead")
    ap.add_argument("--target-roblox", action="store_const", dest="target", const="roblox", help="triangle budget 20,000 (Roblox's official 'individual meshes cannot exceed 20,000 triangles'), texture-max 1024 (Roblox Studio's own diffuse/normal/roughness/metallic cap) -- see create.roblox.com/docs/art/modeling/specifications")
    ap.add_argument("--target-gltf-viewer", action="store_const", dest="target", const="gltf-viewer", help="texture-max 1024, decimate-ratio 0.6 -- a lighter, faster-loading preset than --target-web for casual inspection in a generic web-based glTF viewer; no single platform publishes an official numeric spec for this category")
    ap.add_argument("--target-quicklook", action="store_const", dest="target", const="quicklook", help="triangle budget 100,000, texture-max 2048 -- Apple's own published guidance for AR Quick Look/USDZ (developer.apple.com WWDC24 'Optimize your 3D assets for spatial computing'); also keep the exported file under 10MB for instant loading, not something this flag alone can guarantee")
    ap.add_argument("--draco", action="store_true", help="compress geometry with Draco (delegates to gltf-transform; glb/gltf output only)")
    ap.add_argument("--meshopt", action="store_true", help="compress geometry/animation with Meshopt (delegates to gltf-transform; glb/gltf output only; Blender itself cannot re-import the result -- see references/pitfalls.md)")
    ap.add_argument("--ktx2", action="store_true", help="compress textures to KTX2/Basis (delegates to gltf-transform + the KTX-Software `ktx` CLI; glb/gltf output only)")
    _common.add_common_args(ap, fast=False, progress=False)
    args = ap.parse_args()

    if args.draco and args.meshopt:
        ap.error("--draco and --meshopt are alternative geometry compressors -- give at most one")
    if args.fix_colorspace and args.texture_colorspace:
        ap.error("--fix-colorspace (per-socket correction) and --texture-colorspace (force everything to one value) are alternatives -- give at most one")
    if args.texture_max and args.texture_auto_resolution:
        ap.error("--texture-max (one flat cap) and --texture-auto-resolution (a computed per-texture cap) are alternatives -- give at most one")

    try:
        in_path = _common.require_exists(args.input, "input")
        in_fmt = _formats.detect_format(str(in_path))
        out_fmt = _formats.detect_format(args.out)
        if in_fmt is None or out_fmt is None:
            raise SkillError("unrecognized file extension on input or output", kind="input")
        if (args.draco or args.meshopt or args.ktx2) and out_fmt != "gltf":
            raise SkillError("--draco/--meshopt/--ktx2 need a glb/gltf output (gltf-transform doesn't operate on other formats)", kind="input")
        if args.webp and out_fmt != "gltf":
            raise SkillError("--webp needs a glb/gltf output (EXT_texture_webp is a glTF extension)", kind="input")
        if args.max_bones is not None and args.max_bones < 1:
            raise SkillError("--max-bones must be at least 1", kind="input")
        if args.keyframe_decimate is not None and (not math.isfinite(args.keyframe_decimate) or args.keyframe_decimate <= 0):
            raise SkillError("--keyframe-decimate must be a finite number greater than 0", kind="input")

        bpy_args = {
            "path": str(in_path.resolve()), "format": in_fmt,
            "output": str(Path(args.out).resolve()), "output_format": out_fmt,
            "decimate_ratio": args.decimate_ratio, "fill_holes": args.fill_holes, "weld_doubles": args.weld_doubles,
            "recalc_normals": args.recalc_normals, "triangulate": args.triangulate,
            "texture_max": args.texture_max, "purge_unused": args.purge_unused,
            "texture_auto_resolution": args.texture_auto_resolution, "viewport_width": args.viewport_width,
            "target": args.target, "fix_scale": args.fix_scale, "origin": args.origin,
            "point_thin_voxel": args.point_thin_voxel,
            "webp": args.webp, "webp_quality": args.webp_quality,
            "fix_colorspace": args.fix_colorspace, "texture_colorspace": args.texture_colorspace,
            "remove_unused_bones": args.remove_unused_bones, "max_bones": args.max_bones,
            "keyframe_decimate": args.keyframe_decimate,
            "remove_unused_shape_keys": args.remove_unused_shape_keys,
            "instance_duplicate_meshes": args.instance_duplicate_meshes,
            "merge_materials": args.merge_materials,
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
    if args.webp:
        # Measured, never assumed: WebP at default quality can end up *larger* than a
        # well-compressed PNG for some textures (confirmed -- see references/pitfalls.md), so
        # report the file's own real before/after image bytes rather than a claimed reduction.
        before_bytes = _gltf_extensions.total_image_bytes(str(in_path)) if in_fmt == "gltf" else None
        after_bytes = _gltf_extensions.total_image_bytes(out_str)
        data["texture_bytes_before"] = before_bytes
        data["texture_bytes_after"] = after_bytes
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
        if data["holes_filled"]:
            print(f"  filled {data['holes_filled']} hole(s)")
        if data["scales_fixed"]:
            print(f"  applied scale on {data['scales_fixed']} object(s)")
        if data["points_thinned"]:
            print(f"  thinned {data['points_thinned']} point(s) from the point cloud")
        for t in data["textures_resized"]:
            cap_note = f" (auto cap {t['cap']})" if "cap" in t else ""
            print(f"  texture {t['name']}: {t['from']} -> {t['to']}{cap_note}")
        if args.webp:
            before, after = data.get("texture_bytes_before"), data.get("texture_bytes_after")
            if before is not None and after is not None:
                direction = "smaller" if after < before else "LARGER" if after > before else "unchanged"
                print(f"  webp: texture bytes {before} -> {after} ({direction} -- measured, not assumed)")
            else:
                print(f"  webp: texture bytes after = {after}")
        if data["orphan_data_purged"]:
            print(f"  purged {data['orphan_data_purged']} orphan data block(s)")
        if data["colorspace_fixed"]:
            print(f"  fixed colorspace on {data['colorspace_fixed']} texture reference(s)")
        if data["texture_colorspace_changed"]:
            print(f"  set colorspace to {args.texture_colorspace!r} on: {', '.join(data['texture_colorspace_changed'])}")
        if data["bones_removed"]:
            print(f"  removed {len(data['bones_removed'])} unused bone(s): {', '.join(data['bones_removed'])}")
        if data["bones_demoted"]:
            for d in data["bones_demoted"]:
                print(f"  bone {d['bone']} removed, weight reassigned to {d['reassigned_to']}")
        if data["keyframe_points_before"]:
            kf_pct = (1 - data["keyframe_points_after"] / data["keyframe_points_before"]) * 100
            print(f"  keyframes: {data['keyframe_points_before']} -> {data['keyframe_points_after']} ({kf_pct:.0f}% reduction)")
        if data["shape_keys_removed"]:
            print(f"  removed {len(data['shape_keys_removed'])} unused shape key(s): {', '.join(s['shape_key'] for s in data['shape_keys_removed'])}")
        for g in data["meshes_instanced"]:
            print(f"  instanced {', '.join(g['merged_objects'])} onto {g['canonical_mesh']} ({len(g['datablocks_removed'])} datablock(s) freed)")
        for g in data["materials_merged"]:
            print(f"  merged material(s) {', '.join(g['merged_materials'])} onto {g['canonical_material']}")
        for d in data["delegated"]:
            print(f"  {d['tool']}: ok" if d["ran"] else f"  {d['tool']}: skipped ({d['reason']})")
        for w in data.get("warnings", []):
            print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
